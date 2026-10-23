"""実機確認（2026-10-23）の修正チケット AN-F のうち、画面まわりの回帰スモーク（mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_fix_ui_smoke.py

AN-F-1 animset を作ったあと「先に animset を作ってください」が消える / AN-F-3 ブレンドタブの既定の遷移列がクリップの増減に追従する
（編集済みなら触らない）・「既定の列に戻す」。
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
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_fix_ui_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import session as S
    from tdrive_anim import ui_blend, ui_clips

    project.set_root(tmp)
    make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    s = S.current()
    clips_tab = ui_clips.ClipsTab(s)
    blend = ui_blend.BlendTab(s)
    for t in (clips_tab, blend):
        s.listeners.append(t.refresh)
        t.refresh()
    check("AN-F-1: animset が無いときは案内が出る", "先に animset" in clips_tab.message.text() and "先に animset" in blend.message.text())
    s.new("MisotenChara")
    check("AN-F-1: animset を作ると案内が消える（クリップタブ）", "先に animset" not in clips_tab.message.text(), clips_tab.message.text())
    check("AN-F-1: animset を作ると案内が消える（ブレンドタブ）", "先に animset" not in blend.message.text(), blend.message.text())
    # 別の警告は、animset を作っても勝手に消さない
    blend._say("⚠ 別の警告")
    blend.refresh()
    check("AN-F-1: 別のメッセージは消さない", blend.message.text() == "⚠ 別の警告")

    # ---- AN-F-3: Idle / Run だけで開いたあと Jump を足すと 3 行になる
    s.add_clip_from_range("Idle", 0, 30)
    s.add_clip_from_range("Run", 100, 118, direction="F")
    blend.refresh()
    check("AN-F-3: Idle / Run だけだと 2 行", [r["clip"] for r in blend.rows] == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F"], str(blend.rows))
    s.add_clip_from_range("Jump", 200, 230)
    blend.refresh()
    check("AN-F-3: Jump を足すと既定の列が 3 行に作り直される", [r["clip"] for r in blend.rows] == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F", "A_MisotenChara_Jump"], str(blend.rows))
    check("AN-F-3: 表も 3 行", blend.rows_table.rowCount() == 3)
    # 編集したら触らない
    blend.rows[0]["holdSec"] = 2.5
    s.add_clip_from_range("Wave", 300, 330)
    blend.refresh()
    check("AN-F-3: 手で編集した列はクリップを足しても作り直さない", len(blend.rows) == 3 and blend.rows[0]["holdSec"] == 2.5, str(blend.rows))
    blend.add_row()
    blend.refresh()
    check("AN-F-3: 行を足した列も作り直さない", len(blend.rows) == 4)
    # 既定の列に戻す
    blend.reset_rows()
    check("AN-F-3: 「既定の列に戻す」で 3 行・既定の値に戻る", [r["clip"] for r in blend.rows] == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F", "A_MisotenChara_Jump"] and blend.rows[0]["holdSec"] == 1.0 and blend.rows_table.rowCount() == 3, str(blend.rows))
    s.remove_clip("A_MisotenChara_Wave")
    s.remove_clip("A_MisotenChara_Jump")
    blend.refresh()
    check("AN-F-3: 既定のままならクリップを減らしても追従する", [r["clip"] for r in blend.rows] == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F"], str(blend.rows))
    # ---- AN-F-4: ホイールはフォーカス時だけ
    from PySide6 import QtCore, QtGui

    from tdrive import widgets as WG
    from tdrive_anim import ui_export, ui_shot
    from tdrive_toon import ui_look

    panels = [clips_tab, blend, ui_export.ExportTab(s), ui_shot.ShotTab(s)]
    kinds = (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox, QtWidgets.QComboBox, QtWidgets.QSlider)
    found = [w for p in panels for w in p.findChildren(QtWidgets.QWidget) if isinstance(w, kinds)]
    check("AN-F-4: Animation タブにスピンボックス・コンボ・スライダーがある", len(found) >= 10, str(len(found)))
    not_wg = [type(w).__name__ for w in found if not isinstance(w, WG.WheelOnFocusMixin)]
    check("AN-F-4: すべて共通ウィジェット（ホイールはフォーカス時だけ）", not not_wg, str(not_wg[:5]))
    blend.rows_table.setRowCount(0)
    blend._fill_rows()
    combo = blend.rows_table.cellWidget(0, 0)
    check("AN-F-4: 表のセルのコンボも共通ウィジェット", combo is not None and isinstance(combo, WG.WheelOnFocusMixin))

    def wheel(w) -> bool:
        ev = QtGui.QWheelEvent(QtCore.QPointF(3, 3), QtCore.QPointF(3, 3), QtCore.QPoint(0, 0), QtCore.QPoint(0, 120),
                               QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(w, ev)
        return ev.isAccepted()

    spin = WG.DoubleSpinBox()
    spin.setRange(0, 100)
    spin.setValue(10)
    cb = WG.ComboBox()
    cb.addItems(["a", "b", "c"])
    cb.setCurrentIndex(1)
    sl = WG.Slider(QtCore.Qt.Horizontal)
    sl.setRange(0, 100)
    sl.setValue(50)
    for w in (spin, cb, sl):
        w.show()
    check("AN-F-4: フォーカスが無いとホイールでは変わらない（親へ渡す）",
          not wheel(spin) and spin.value() == 10 and not wheel(cb) and cb.currentIndex() == 1 and not wheel(sl) and sl.value() == 50)
    sl.activateWindow()
    sl.setFocus()
    APP.processEvents()
    if sl.hasFocus():
        wheel(sl)
        check("AN-F-4: フォーカスがあるとホイールで変わる", sl.value() != 50, str(sl.value()))
    else:
        INFO.append("オフスクリーンでフォーカスが入らないため、フォーカス時のホイールは未確認")
    check("AN-F-4: Toon の ToonSpin / ToonSlider も同じミックスイン", issubclass(ui_look.ToonSpin, WG.WheelOnFocusMixin) and issubclass(ui_look.ToonSlider, WG.WheelOnFocusMixin) and issubclass(ui_look.ToonIntSpin, WG.WheelOnFocusMixin))
    # ---- AN-F-5: 今のブレンドの割合
    from tdrive_anim import ui_blend as ui_blend_mod

    blend.reset_rows()
    for f, v in ((0, 0), (30, 10), (100, 0), (118, 40)):
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=v)
    blend.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0, "crossFadeSec": 1.0, "switchAt": 1.0, "speed": 1.0},
                  {"clip": "A_MisotenChara_Run_F", "holdSec": 1.5, "crossFadeSec": 1.0, "switchAt": 1.0, "speed": 1.0}]
    blend.build_from_rows()
    check("AN-F-5: 合成中は割合の表示が出る", "今のブレンド" in blend.mix_label.text() and "Idle 100%" in blend.mix_label.text(), blend.mix_label.text())
    cmds.currentTime(45, edit=True)  # 保持 1 秒（30 フレーム）のあと 1 秒（30 フレーム）で Idle → Run
    blend.update_mix()
    check("AN-F-5: 切り替えの途中は割合が変わる（Idle 50% / Run 50%）", "Idle 50%" in blend.mix_label.text() and "Run_F 50%" in blend.mix_label.text() and "f=45" in blend.mix_label.text(), blend.mix_label.text())
    cmds.currentTime(80, edit=True)
    blend.update_mix()
    check("AN-F-5: 切り替えのあとは Run だけ", "Run_F 100%" in blend.mix_label.text() and "Idle" not in blend.mix_label.text(), blend.mix_label.text())
    hud = ui_blend_mod.HUD_NAME
    hud_exists = cmds.headsUpDisplay(hud, exists=True)
    INFO.append(f"HUD（batch で作れるか）: {hud_exists}")
    if hud_exists:
        check("AN-F-22: 合成中はビューポートの HUD にも割合が出る", "Run_F" in (cmds.headsUpDisplay(hud, query=True, label=True) or ""))
    blend.remove()
    check("AN-F-22: 合成を外すと HUD が消える", not cmds.headsUpDisplay(hud, exists=True))
    check("AN-F-5: 合成を外すと表示が消える", blend.mix_label.text() == "")
    # ---- AN-F-10: 命名タブが Maya の Undo / 時間単位の変化に追従する
    from tdrive_anim import ui_naming

    cmds.undoInfo(state=True)
    recorded: list[dict] = []
    orig_job = cmds.scriptJob
    cmds.scriptJob = lambda **kw: (recorded.append(kw), len(recorded))[1]
    try:
        nt = ui_naming.NamingTab(s)
        nt.request_reinspect()
        check("AN-F-10: 検査する前は自動では検査しない", not nt._stale and not recorded)
        nt.inspect()
        events = [kw["event"][0] for kw in recorded]
        check("AN-F-10: 最初の検査で Undo / Redo / timeUnitChanged / NameChanged の scriptJob を登録", events == ["Undo", "Redo", "timeUnitChanged", "NameChanged"], str(events))
        mesh_before = [f.name for f in nt.report.findings if f.kind == "mesh"]
        mesh = make_mini_rig.MESH
        cmds.rename(mesh, "RenamedMesh")
        nt.request_reinspect()
        check("AN-F-10: 変化を受けると 0.3 秒まとめてから検査し直す予約になる", nt._stale and nt._reinspect_timer.isActive())
        nt._reinspect(force=True)
        check("AN-F-10: 改名のあと一覧の「今の名前」が変わる", any("RenamedMesh" in f.name for f in nt.report.findings if f.kind == "mesh"), str([f.name for f in nt.report.findings]))
        cmds.undo()
        nt.request_reinspect()
        nt._reinspect(force=True)
        check("AN-F-10: Maya の Undo のあと一覧の「今の名前」が戻る", [f.name for f in nt.report.findings if f.kind == "mesh"] == mesh_before, str([f.name for f in nt.report.findings]))
        cmds.currentUnit(time="film")
        nt.request_reinspect()
        nt._reinspect(force=True)
        check("AN-F-10: 単位を変えるとシーン設定の表示が変わる", any(i.key == "time" for i in nt.report.settings))
        cmds.currentUnit(time="ntsc")
        nt.detach()
        check("AN-F-10: detach で scriptJob を外す", nt._jobs == [] and ui_naming._active_tab is None)
    finally:
        cmds.scriptJob = orig_job

    # ---- AN-F-12: エディタの Ctrl+Z で命名タブの Maya 操作が戻る
    from tdrive_anim import scene_naming as SN2

    cmds.undoInfo(state=True)
    cmds.spaceLocator(name="LocA")
    with SN2.undo_chunk("tdAnimRename"):
        cmds.rename("LocA", "LocB")
    s.push_maya_undo("tdAnimRename")
    s.add_clip_from_range("Tmp", 0, 10)  # そのあとツールの操作（animset）
    check("AN-F-12: 履歴に Maya の印が積まれる", any("maya" in e for e in s._undo))
    check("AN-F-12: 1 回目の Ctrl+Z は animset の操作", s.undo() is True and cmds.objExists("LocB"))
    check("AN-F-12: 2 回目の Ctrl+Z で Maya の改名が戻る", s.undo() is True and cmds.objExists("LocA") and not cmds.objExists("LocB"))
    check("AN-F-12: Ctrl+Y で Maya の改名をやり直す", s.redo() is True and cmds.objExists("LocB"))
    with SN2.undo_chunk("tdOther"):
        cmds.rename("LocB", "LocC")
    ok = s.undo()
    check("AN-F-12: 間に別の Maya 操作があると戻さず、ビューポートでの Ctrl+Z を案内する", ok is False and "ビューポート" in s.last_undo_message and cmds.objExists("LocC"), s.last_undo_message)
    cmds.undo()  # 別の操作をビューポートで戻す
    check("AN-F-12: ビューポートで戻したあとはエディタの Ctrl+Z で戻せる", s.undo() is True and cmds.objExists("LocA"), s.last_undo_message)

    # ---- AN-F-9 / AN-F-11: テクスチャ検査は割り当て済みマテリアルに届く file だけ・部位名はマテリアル由来 / 掃除候補は同じマテリアルを 1 行に
    make_mini_rig.add_materials()
    for i in range(4):  # Hair の SG をあと 4 つ（割り当ての無い SG）。同じマテリアルが SG ごとに並ぶ状況（AN-F-11）
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"UnusedHairSG{i}")
        cmds.connectAttr("Unused.outColor", sg + ".surfaceShader", force=True)
    uuid_a, uuid_b = "0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"
    f_hair = cmds.shadingNode("file", asTexture=True, name="file_hair")
    cmds.setAttr(f_hair + ".fileTextureName", f"C:/tex/{uuid_a}.png", type="string")
    cmds.connectAttr(f_hair + ".outColor", "Hair.color", force=True)
    f_unused = cmds.shadingNode("file", asTexture=True, name="file_unused")
    cmds.setAttr(f_unused + ".fileTextureName", f"C:/tex/{uuid_b}.png", type="string")
    cmds.connectAttr(f_unused + ".outColor", "Unused.color", force=True)
    rep = SN2.inspect_scene("MisotenChara", s.data)
    tex = [f for f in rep.findings if f.kind == "texture"]
    check("AN-F-9: 未割り当てのマテリアルにつながる file は検査に出ない", len(tex) == 1 and uuid_b not in " ".join(f.name for f in tex), str([f.name for f in tex]))
    check("AN-F-9: 割り当て済みの file は部位名がマテリアル由来（Hair）", tex and tex[0].suggestion == "T_MisotenChara_Hair_BaseColor.png", str([f.suggestion for f in tex]))
    from tdrive_anim.core import naming_rules as NR

    check("AN-F-9: HairM → Hair / SkinM2 → Skin", NR.part_from_material("HairM", "MisotenChara") == "Hair" and NR.part_from_material("SkinM2", "MisotenChara") == "Skin")
    items = [i for i in rep.cleanup if i.kind == "material"]
    check("AN-F-11: 同じマテリアルは掃除候補の 1 行にまとまる", len(items) == 1 and "Unused" in items[0].label, str([i.label for i in rep.cleanup]))
    check("AN-F-11: 消すときはその SG を全部消す（5 つ）", sorted(n for n in items[0].nodes if n.endswith(tuple("01234")) or n == "UnusedSG").__len__() == 5 if items else False, str(items[0].nodes if items else None))
    SN2.apply_cleanup(items)
    check("AN-F-11: 削除で Unused と 5 つの SG が消える", not cmds.objExists("Unused") and not cmds.ls("UnusedSG", "UnusedHairSG*"))

    # ---- AN-F-20: 遷移列の表が animset の変更の直後に更新される（前に出ているブレンドタブ）
    for nm_ in list(s.data["clips"]):
        s.remove_clip(nm_["name"])
    from tdrive_anim.ui import AnimPanel as AP20

    panel20 = AP20()
    panel20.tabs.setCurrentWidget(panel20.blend_tab)
    s.add_clip_from_range("Idle", 0, 30)
    s.add_clip_from_range("Run", 100, 118, direction="F")
    rows_after_two = panel20.blend_tab.rows_table.rowCount()
    s.add_clip_from_range("Jump", 200, 230)
    check("AN-F-20: クリップを足した直後に遷移列の表の行が増える（2 → 3）", rows_after_two == 2 and panel20.blend_tab.rows_table.rowCount() == 3, f"{rows_after_two} {panel20.blend_tab.rows_table.rowCount()}")
    first_cell = panel20.blend_tab.rows_table.cellWidget(2, 0)
    check("AN-F-20: 3 行目のクリップのコンボが Jump", first_cell is not None and first_cell.currentText() == "A_MisotenChara_Jump")
    s.remove_clip("A_MisotenChara_Jump")
    check("AN-F-20: クリップを消した直後に行が減る", panel20.blend_tab.rows_table.rowCount() == 2)
    s.undo()
    check("AN-F-20: Undo の直後にも表が戻る", panel20.blend_tab.rows_table.rowCount() == 3)
    panel20.detach()

    # ---- AN-F-15 / AN-F-25: ショットの再生はシーケンス時間で駆動する（QTimer のトランスポート）
    from PySide6 import QtCore as QC2

    from tdrive_anim import shot_scene as SS

    cam_a = cmds.camera(name="CAM_Main")[0]
    cam_b = cmds.camera(name="CAM_Close")[0]
    cmds.camera(name="CAM_Orig")
    shot = {"shot": "Opening01", "frameRate": 30, "range": [0, 240], "cameras": [{"name": "CAM_Main", "node": cam_a, "start": 0, "end": 120},
                                                                                  {"name": "CAM_Close", "node": cam_b, "start": 121, "end": 240}]}
    looks: list[str] = []
    seq: list[int] = []
    binds: list[str] = []
    saved = (SS._model_panel, SS._panel_camera, SS._look_thru, SS._set_sequence_time, SS._bind_sequencer)
    SS._model_panel, SS._panel_camera, SS._look_thru = (lambda: "modelPanel4", lambda p: "CAM_Orig1", lambda p, c: looks.append(c))
    SS._set_sequence_time, SS._bind_sequencer = (lambda f: seq.append(f)), (lambda p: binds.append(p))
    try:
        check("AN-F-25: 再生を始める（Maya の play は使わない）", SS.play(shot) is True and SS.is_playing() and not cmds.play(query=True, state=True) and seq == [0])
        check("AN-F-25: パネルを sequenceManager に結ぶ（lookThru は使わない）", binds == ["modelPanel4"] and looks == [], f"{binds} {looks}")
        check("AN-F-25: QTimer の間隔がシーンの fps（30 fps → 33 ms）", isinstance(SS._transport["timer"], QC2.QTimer) and SS._transport["timer"].interval() == 33)
        check("AN-F-25: 1 ティックずつ進めると sequenceManager の時刻が進む", SS.transport_step(1) and SS.transport_step(120) and SS.transport_step(121) and seq == [0, 1, 120, 121], str(seq))
        check("AN-F-25: 同じフレームでは繰り返さない", SS.transport_step(121) and seq[-1] == 121 and seq.count(121) == 1)
        check("AN-F-25: 範囲の最後で False（止まる）", SS.transport_step(240) is False and seq[-1] == 240)
        SS.stop()
        check("AN-F-25: 止めたらタイマーが止まり、lookThru で元のカメラに戻す（結び付けは外れたまま。結び直すのは次の ▶）", not SS.is_playing() and looks == ["CAM_Orig1"] and binds == ["modelPanel4"], f"{looks} {binds}")
    finally:
        SS._model_panel, SS._panel_camera, SS._look_thru, SS._set_sequence_time, SS._bind_sequencer = saved
    # 本物の Camera Sequencer: 121 フレーム以降は CAM_Close の shot になる（パネルは無いのでカメラの切り替わりは shot の currentCamera で確認）
    s.new_shot("Seq01", 30, 0, 240)
    s.shot_add_camera(cam_a, 0, 120)
    s.shot_add_camera(cam_b, 121, 240)
    SS.sync_cameras(s.shot)
    SS._transport.update(shot=s.shot, lo=0, hi=240, last=None)
    SS.transport_step(50)
    cur_a = cmds.sequenceManager(query=True, currentShot=True)
    SS.transport_step(130)
    cur_b = cmds.sequenceManager(query=True, currentShot=True)
    cams = (cmds.listConnections(f"{cur_a}.currentCamera", source=True, destination=False) or [""])[0], (cmds.listConnections(f"{cur_b}.currentCamera", source=True, destination=False) or [""])[0]
    check("AN-F-25: 実際の sequenceManager で、50 フレームは CAM_Main・130 フレームは CAM_Close", cams == (cam_a, cam_b) and cmds.currentTime(query=True) == 130, f"{cams} {cmds.currentTime(query=True)}")
    SS._transport.update(shot=None)
    SS.remove("Seq01")
    # ---- AN-F-31: 何度再生しても毎回切り替わる・元のカメラは反映の直前に 1 回だけ覚える
    state31 = {"cam": "persp", "bound": False}
    cams31 = {"cam_a": cam_a, "cam_b": cam_b}

    def look31(p, c):
        state31["cam"], state31["bound"] = c, False  # lookThru は sequenceManager の結び付けを外す（実機の挙動）

    def bind31(p):
        state31["bound"] = True

    def seq31(f):
        if state31["bound"]:
            state31["cam"] = cam_a if f <= 120 else cam_b

    saved31 = (SS._model_panel, SS._panel_camera, SS._look_thru, SS._set_sequence_time, SS._bind_sequencer)
    SS._model_panel, SS._panel_camera, SS._look_thru, SS._set_sequence_time, SS._bind_sequencer = (lambda: "modelPanel4", lambda p: state31["cam"], look31, seq31, bind31)
    try:
        sh31 = {"shot": "Loop31", "frameRate": 30, "range": [0, 240], "cameras": [{"name": "CAM_Main", "node": cam_a, "start": 0, "end": 120},
                                                                                    {"name": "CAM_Close", "node": cam_b, "start": 121, "end": 240}]}
        s.new_shot("Loop31", 30, 0, 240)
        s.shot_add_camera(cam_a, 0, 120)
        s.shot_add_camera(cam_b, 121, 240)
        SS.build(s.shot)
        got31 = []
        for rnd in range(3):
            SS.play(sh31)
            SS.transport_step(10)
            a31 = state31["cam"]
            SS.transport_step(150)
            b31 = state31["cam"]
            got31.append((a31, b31))
            SS.transport_step(240)
            SS.stop()
            got31.append(state31["cam"])
        check("AN-F-31: ▶ → 終了を繰り返しても毎回 50→CAM_Main・150→CAM_Close に切り替わり、終わるたびに persp に戻る",
              got31 == [(cam_a, cam_b), "persp"] * 3, str(got31))
        SS.remove("Loop31")
        check("AN-F-31: 「外す」で元のカメラ（persp）", state31["cam"] == "persp")
        SS.build(s.shot)
        SS.play(sh31)
        SS.transport_step(150)
        c31 = state31["cam"]
        SS.stop()
        check("AN-F-31: 外す → 反映 → ▶ でも切り替わる", c31 == cam_b and state31["cam"] == "persp", f"{c31} {state31}")
        SS.remove("Loop31")
    finally:
        SS._model_panel, SS._panel_camera, SS._look_thru, SS._set_sequence_time, SS._bind_sequencer = saved31

    SS._model_panel = lambda: None
    check("AN-F-25: パネルが無い環境でも再生できる（時刻だけ進む）", SS.play(shot) is True)
    SS.stop()
    SS._model_panel = saved[0]

    # ---- AN-F-16: 反映したパネルのカメラを、外すと元に戻す
    looks2: list[tuple[str, str]] = []
    saved2 = (SS._model_panel, SS._panel_camera, SS._look_thru)
    SS._model_panel, SS._panel_camera, SS._look_thru = (lambda: "modelPanel4", lambda p: "persp", lambda p, c: looks2.append((p, c)))
    try:
        s.new_shot("Refl01", 30, 0, 60)
        s.shot_add_camera(cam_a, 0, 60)
        SS.build(s.shot)
        check("AN-F-16: 反映する前のパネルのカメラを覚える", SS._reflected_camera.get("Refl01") == ("modelPanel4", "persp"), str(SS._reflected_camera))
        SS.remove("Refl01")
        check("AN-F-16: 外すとパネルのカメラが persp に戻る", looks2 == [("modelPanel4", "persp")] and "Refl01" not in SS._reflected_camera, str(looks2))
    finally:
        SS._model_panel, SS._panel_camera, SS._look_thru = saved2

    # ---- AN-F-23: 出力タブの「合成を外す」
    from tdrive_anim import composition as K23
    from tdrive_anim.core import sequence as Seq23

    from tdrive_anim.ui import AnimPanel as AP23

    panel23 = AP23()
    ex23 = panel23.export_tab
    clips23 = {c["name"]: c for c in s.data["clips"]}
    cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=0, value=0)
    cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=30, value=9)
    K23.compose("blend", Seq23.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}], clips23, 30), make_mini_rig.JOINTS, 30)
    ex23._update_buttons()
    check("AN-F-23: 合成中は出力タブに「合成を外す」が出て、出力ボタンは灰色", not ex23.unblend_btn.isHidden() and not ex23.model_btn.isEnabled())
    n23 = ex23.remove_compositions()
    check("AN-F-23: 押すと合成が外れ、出力ボタンが有効になる", n23 == 1 and K23.active() == [] and "合成" not in ex23.block_reason() and ex23.unblend_btn.isHidden(), f"{n23} {K23.active()} {ex23.block_reason()!r} {ex23.unblend_btn.isHidden()}")
    panel23.detach()

    # ---- AN-F-27 / AN-F-28: 状態図のパラメータ操作
    from tdrive_anim.ui import AnimPanel as AP27

    panel27 = AP27()
    b27 = panel27.blend_tab
    panel27.tabs.setCurrentWidget(b27)
    check("AN-F-28: 「MS2026 の状態図から」ボタンが無い", not hasattr(b27, "graph_btn"))
    check("AN-F-28: 見出しが「状態図（Idle / Run / Jump）をパラメータで動かす」", any(g.title().startswith("状態図（Idle") for g in b27.findChildren(QtWidgets.QGroupBox)))
    b27.reset_timeline()
    b27.graph = None
    b27.mode = "rows"
    b27.speed.setSliderDown(True)
    for v in (10, 20, 30, 40, 50):  # ドラッグ中
        b27.speed.setValue(v)
    check("AN-F-27: ドラッグ中は記録しない", b27.timeline == [], str(b27.timeline))
    b27.speed.setSliderDown(False)  # 離す → sliderReleased
    check("AN-F-28: スライダーを動かすと既定の状態図が自動で使われる", b27.mode == "graph" and b27.graph is not None)
    check("AN-F-27: ドラッグを離すと記録は 1 件（最後の値）", len(b27.timeline) == 1 and b27.timeline[0][1]["Speed"] == 0.5, str(b27.timeline))
    rows27 = [[b27.timeline_table.item(r, c).text() for c in range(4)] for r in range(b27.timeline_table.rowCount())]
    check("AN-F-27: 記録の表は マーカーごと 1 行（時刻 / パラメータ / 値 / 結果の状態）", rows27[1][:3] == ["f=0（0.00s）", "Speed", "0.5"], str(rows27))
    check("AN-F-27: 評価した遷移列が一行で出る", b27.switch_label.text().startswith("評価した遷移列: 0.00s Idle → ") and "Run（0.08s）" in b27.switch_label.text(), b27.switch_label.text())
    check("AN-F-27: 「長さ」の既定は記録の最後 + 2 秒", abs(b27.total_sec.value() - 2.0) < 1e-9, str(b27.total_sec.value()))
    b27.total_sec.setValue(6.0)
    b27.speed.setValue(0)
    check("AN-F-27: 手で変えた「長さ」は自動で変えない", abs(b27.total_sec.value() - 6.0) < 1e-9)
    b27.reset_graph()
    check("AN-F-28: 「状態図を既定に戻す」", b27.graph is not None and "既定" in b27.message.text())
    # rows の合成のままパラメータを動かすと案内
    b27.reset_timeline()
    b27.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0}]
    b27.mode = "rows"
    b27.build_from_rows()
    b27.speed.setValue(80)
    check("AN-F-27: 遷移列の合成のままパラメータを動かすと「状態図で合成を作る」を案内", "状態図で合成を作る" in b27.message.text() and "反映されません" in b27.message.text(), b27.message.text())
    b27.remove()
    # 記録は最大 50 行
    for i in range(60):
        b27.timeline = __import__("tdrive_anim.core.state_machine", fromlist=["x"]).with_event(b27.timeline, i * 0.1, {"Speed": (i % 2) * 0.5})
    b27._fill_graph_info()
    check("AN-F-27: 記録の表は最大 50 行（古いものは畳む）", b27.timeline_table.rowCount() <= 50, str(b27.timeline_table.rowCount()))

    # ---- AN-F-29: パラメータのタイムライン
    from tdrive_anim.core import state_machine as M29

    tv = b27.timeline_view
    b27.reset_timeline()
    b27.timeline = M29.with_event(b27.timeline, 1.0, {"Speed": 0.5})
    b27.timeline = M29.with_event(b27.timeline, 2.0, {"ActionState": 2})
    b27._fill_graph_info()
    check("AN-F-29: タイムラインのマーカーが記録と一致", tv.markers == [(1.0, "Speed", 0.5), (2.0, "ActionState", 2.0)], str(tv.markers))
    r29 = M29.evaluate(b27.graph, b27.timeline, b27.total_sec.value(), b27._fps())
    check("AN-F-29: 状態の帯が state_machine.evaluate の結果と一致", tv.bands == M29.state_bands(r29, b27.total_sec.value()) and [b[2] for b in tv.bands][:2] == ["Idle", "Run"], str(tv.bands))
    tv.selectionChanged.emit("Speed", 1.0)
    check("AN-F-29: タイムラインで選ぶと表の行も選ばれる", [i.row() for i in b27.timeline_table.selectedIndexes()][:1] == [0], str([i.row() for i in b27.timeline_table.selectedIndexes()]))
    b27.timeline_table.selectRow(1)
    check("AN-F-29: 表で選ぶとタイムラインのマーカーも選ばれる", tv.selected == ("ActionState", 2.0), str(tv.selected))
    tv.markerMoved.emit("ActionState", 2.0, 1.5)
    check("AN-F-29: マーカーを動かすと記録が動く", (1.5, "ActionState", 2.0) in M29.markers(b27.timeline) and not any(t == 2.0 for t, _, _ in M29.markers(b27.timeline)), str(b27.timeline))
    tv.deleteRequested.emit("Speed", 1.0)
    check("AN-F-29: 削除すると記録から消える", [m[1] for m in M29.markers(b27.timeline)] == ["ActionState"], str(b27.timeline))
    tv.addRequested.emit(60, "Speed")
    check("AN-F-29: ダブルクリック（追加）で、そのフレームに今の値が記録される", any(abs(t - 2.0) < 1e-9 and p == "Speed" for t, p, _ in M29.markers(b27.timeline)), str(b27.timeline))
    check("AN-F-29: 記録はシーンの fileInfo に持つ（animset には入れない）", "Speed" in (cmds.fileInfo(ui_blend_mod.TIMELINE_KEY, query=True) or [""])[0] and "timeline" not in str(s.data.keys()))
    saved_tl = list(b27.timeline)
    b27.timeline, b27._timeline_loaded = [], False
    b27._load_timeline()
    check("AN-F-29: 開き直し（fileInfo）で記録が戻る", b27.timeline == saved_tl, str(b27.timeline))
    # 描画とマウス操作（QTest）
    from PySide6 import QtCore as QC29
    from PySide6.QtTest import QTest

    tv.resize(640, 120)
    img = tv.grab()
    check("AN-F-29: タイムラインが描画できる", not img.isNull() and img.width() > 100, f"{img.isNull()} {img.width()}x{img.height()}")
    b27.timeline = [(1.0, {"ActionState": 2.0})]
    b27._fill_graph_info()
    rect = tv._marker_rect(1.0, "ActionState")
    center = QC29.QPoint(int(rect.center().x()), int(rect.center().y()))
    QTest.mouseClick(tv, QC29.Qt.LeftButton, QC29.Qt.NoModifier, center)
    check("AN-F-29: マーカーをクリックで選択", tv.selected == ("ActionState", 1.0), str(tv.selected))
    target_x = int(tv.t_to_x(2.0))
    QTest.mousePress(tv, QC29.Qt.LeftButton, QC29.Qt.NoModifier, center)
    QTest.mouseMove(tv, QC29.QPoint(target_x, center.y()))
    QTest.mouseRelease(tv, QC29.Qt.LeftButton, QC29.Qt.NoModifier, QC29.QPoint(target_x, center.y()))
    moved = [m for m in M29.markers(b27.timeline) if m[1] == "ActionState"]
    check("AN-F-29: ドラッグでマーカーが動く（フレームにスナップ）", len(moved) == 1 and abs(moved[0][0] * 30 - round(moved[0][0] * 30)) < 1e-6 and abs(moved[0][0] - 2.0) < 0.1, str(moved))
    QTest.keyClick(tv, QC29.Qt.Key_Delete)
    check("AN-F-29: Delete キーでマーカーが消える", not M29.markers(b27.timeline), str(b27.timeline))
    QTest.mouseDClick(tv, QC29.Qt.LeftButton, QC29.Qt.NoModifier, QC29.QPoint(int(tv.t_to_x(3.0)), 10))
    check("AN-F-29: ダブルクリックで上段（ActionState）のそのフレームに追加", any(m[1] == "ActionState" and abs(m[0] - 3.0) < 1e-6 for m in M29.markers(b27.timeline)), str(b27.timeline))
    # AN-F-34 / AN-F-32 / AN-F-33
    b27.timeline = [(3.0, {"ActionState": 2.0})]
    b27._fill_graph_info()
    check("AN-F-34: 表の時刻にフレームと秒の両方（f=90（3.00s））", b27.timeline_table.item(0, 0).text() == "f=90（3.00s）", b27.timeline_table.item(0, 0).text())
    check("AN-F-34: 目盛りの間隔がフレーム（30 の倍数など）", tv._tick_step_frames() in (5, 10, 15, 30, 60, 90, 120, 150, 300, 600, 900))
    tv.select("ActionState", 3.0)
    tv.selectionChanged.emit("ActionState", 3.0)
    check("AN-F-34: 選択の同期（フレーム表記の表）", [i.row() for i in b27.timeline_table.selectedIndexes()][:1] == [0])
    cmds.currentTime(5, edit=True)
    far = QC29.QPoint(int(tv.t_to_x(1.0)), 40)  # マーカーの無い所
    QTest.mouseClick(tv, QC29.Qt.LeftButton, QC29.Qt.NoModifier, far)
    check("AN-F-32: マーカーの上以外をクリックすると Maya の現在時刻がそのフレームへ動く（1.0 秒 = 30）", cmds.currentTime(query=True) == 30, str(cmds.currentTime(query=True)))
    QTest.mousePress(tv, QC29.Qt.LeftButton, QC29.Qt.NoModifier, far)
    QTest.mouseMove(tv, QC29.QPoint(int(tv.t_to_x(1.5)), 40))
    QTest.mouseRelease(tv, QC29.Qt.LeftButton, QC29.Qt.NoModifier, QC29.QPoint(int(tv.t_to_x(1.5)), 40))
    check("AN-F-32: ドラッグして離すと、離した位置のフレームになる（45）", cmds.currentTime(query=True) == 45, str(cmds.currentTime(query=True)))
    b27.timeline = []
    b27.speed_spin.setValue(0.37)
    b27.speed_spin.editingFinished.emit()
    check("AN-F-33: 数値入力で Speed の記録が 1 件増える（0.37）", len(b27.timeline) == 1 and abs(b27.timeline[0][1]["Speed"] - 0.37) < 1e-9 and b27.speed.value() == 37, str(b27.timeline))
    b27.speed.setValue(60)
    check("AN-F-33: スライダーを動かすと数値欄も合う", abs(b27.speed_spin.value() - 0.60) < 1e-9, str(b27.speed_spin.value()))

    # まとめて作成
    check("AN-F-29: 「変更のたびに作り直す」は既定オフ", not b27.auto_rebuild.isChecked())
    b27.reset_timeline()
    b27.mode = "graph"
    b27.build_from_graph()
    first_nodes = set(cmds.ls("tdAnimComp_*", type="network"))
    builds: list[int] = []
    orig_compose = b27._compose
    b27._compose = lambda pls, use_cache=False: (builds.append(1), orig_compose(pls, use_cache))[1]
    for fr in (10, 30, 50):
        cmds.currentTime(fr, edit=True)
        b27.speed.setValue(10 + fr)
    b27.flush_rebuild()
    check("AN-F-29: トグルオフなら記録するだけで作り直さず、最上部に「作り直していません」", builds == [] and not b27.pending_label.isHidden() and "作り直していません" in b27.pending_label.text(), f"{builds} {b27.pending_label.text()}")
    b27.rebuild_now()
    check("AN-F-29: 「作り直す」で 1 回だけ合成を作り、表示が消える", builds == [1] and b27.pending_label.isHidden(), str(builds))
    b27._compose = orig_compose
    b27.remove()
    panel27.detach()

    # ---- AN-F-30: 「変更のあるクリップだけ」の指紋が、出力形式の版とクリップ属性を含む
    import copy as copy30

    from tdrive_anim import export as X30

    base30 = s.data
    clip30 = base30["clips"][0]
    fp0 = X30.fingerprint(clip30, base30)
    check("AN-F-30: 同じなら同じ指紋", X30.fingerprint(copy30.deepcopy(clip30), base30) == fp0)
    c1 = copy30.deepcopy(clip30)
    c1["loop"] = not c1.get("loop", False)
    check("AN-F-30: loop を変えると指紋が変わる", X30.fingerprint(c1, base30) != fp0)
    c2 = copy30.deepcopy(clip30)
    c2["events"] = [*c2.get("events", []), {"frame": clip30["start"], "key": "Hit"}]
    check("AN-F-30: イベントを足すと指紋が変わる", X30.fingerprint(c2, base30) != fp0)
    c3 = copy30.deepcopy(clip30)
    c3["rootMotion"] = "bake" if c3.get("rootMotion") == "none" else "none"
    check("AN-F-30: rootMotion を変えると指紋が変わる", X30.fingerprint(c3, base30) != fp0)
    v_old = X30.EXPORT_FORMAT_VERSION
    X30.EXPORT_FORMAT_VERSION = v_old + 1
    try:
        check("AN-F-30: 出力形式の版を上げると全クリップの指紋が変わる（再出力される）", X30.fingerprint(clip30, base30) != fp0)
    finally:
        X30.EXPORT_FORMAT_VERSION = v_old
    check("AN-F-30: 出力形式の版が AN-F-24 の自前ベイクで上がっている（3 以上）", v_old >= 3)

    # ---- AN-F-35: 停止ボタンは全部止める
    from tdrive import lifecycle as LC35
    from tdrive_anim import transport as T35
    from tdrive_anim.ui import AnimPanel as AP35
    from tdrive_anim.ui import AnimTool as AT35

    panel35 = AP35()
    idle35 = T35.status_text()
    sh35 = {"shot": "Stop35", "frameRate": 30, "range": [0, 240], "cameras": [{"name": "CAM_Main", "node": cam_a, "start": 0, "end": 240}]}
    SS._model_panel = lambda: None  # パネルが無い環境でも時刻は進む

    def timers_left() -> list[str]:
        left = []
        if SS._transport["timer"] is not None:
            left.append("shot-timer")
        if panel35.blend_tab._rebuild_timer.isActive():
            left.append("rebuild-timer")
        if cmds.play(query=True, state=True):
            left.append("maya-play")
        if SS.is_playing() or MC35.is_playing():
            left.append("flag")
        return left

    from tdrive_anim import clips as MC35

    check("AN-F-35: 最上部に「■ 停止」が出ている", idle35 == "■ 停止" and panel35.transport_label.text() == "■ 停止", idle35)
    SS.play(sh35)
    panel35.blend_tab._rebuild_timer.start()
    panel35._update_transport()
    check("AN-F-35: 再生中は最上部に「▶ 再生中（ショット / 作り直し待ち）」", "ショット" in panel35.transport_label.text() and "作り直し待ち" in panel35.transport_label.text() and panel35.transport_label.text().startswith("▶"), panel35.transport_label.text())
    panel35.clips_tab.stop_btn.click()  # クリップタブの ■ からでも全部止まる
    check("AN-F-35: 「▶ → ■（クリップタブ）」のあとに何も残らない", timers_left() == [], str(timers_left()))
    SS.play(sh35)
    panel35.shot_tab.stop_btn.click()
    check("AN-F-35: 「▶ → ■（ショットタブ）」のあとに何も残らない", timers_left() == [], str(timers_left()))
    SS.play(sh35)
    panel35.blend_tab._rebuild_timer.start()
    panel35.stop_all_btn.click()
    check("AN-F-35: 「■ すべて停止」で全部止まり、表示が「■ 停止」に戻る", timers_left() == [] and panel35.transport_label.text() == "■ 停止", f"{timers_left()} {panel35.transport_label.text()}")
    SS.play(sh35)
    LC35.run_reload_cleanups()
    check("AN-F-35: 「▶ → リロード」のあとに何も残らない", timers_left() == [], str(timers_left()))
    SS.play(sh35)
    AT35().on_scene_opened()
    check("AN-F-35: 「▶ → シーンを開く」のあとに何も残らない", timers_left() == [], str(timers_left()))
    SS.play(sh35)
    panel35.blend_tab._rebuild_timer.start()
    panel35.detach()
    check("AN-F-35: 「▶ → ウィンドウを閉じる」のあとに何も残らず、表示用タイマーも止まる", timers_left() == [] and not panel35._transport_timer.isActive(), str(timers_left()))
    jobs35 = cmds.scriptJob(listJobs=True) or []
    check("AN-F-35: 時刻を動かす追従の scriptJob が残っていない", not [j for j in jobs35 if "shot_scene" in j or "follow" in j.lower()], str(jobs35[:3]))
    SS._model_panel = saved[0]

    # ---- AN-F-36: イベントのキーの接線は out だけ step（in を step にしようとして出る警告を出さない）
    from tdrive_anim.core import shot as SH36

    s.new_shot("Evt36", 30, 0, 60)
    s.shot_add_event(30, "Hit")
    SS.ensure_group(s.shot)
    from maya.api import OpenMaya as OM36

    warned36: list[str] = []
    cb36 = OM36.MCommandMessage.addCommandOutputCallback(lambda msg, kind, _d: warned36.append(msg) if kind == OM36.MCommandMessage.kWarning else None)
    try:
        SS.sync_events(s.shot)
    finally:
        OM36.MMessage.removeCallback(cb36)
    check("AN-F-36: イベントの反映で「ステップに設定できません」の警告が出ない", not [w for w in warned36 if "ステップ" in w or "step" in w.lower()], str(warned36[:2]))
    loc36 = SS.event_locators("Evt36")
    t_in = cmds.keyTangent(loc36[0], attribute="ddEvent", query=True, inTangentType=True)
    t_out = cmds.keyTangent(loc36[0], attribute="ddEvent", query=True, outTangentType=True)
    check("AN-F-36: イベントのキーは outTangent だけ step（inTangent は step にしない）", set(t_out) == {"step"} and "step" not in t_in, f"{t_in} {t_out}")
    SS.remove("Evt36")

    # ---- AN-F-35 の続き: 取り残された古い scriptJob の掃除（batch では scriptJob が動かないので、cmds をモックして名前の判定と kill を確かめる）
    from tdrive_anim import transport as T35b

    killed: list[int] = []
    orig_sj = cmds.scriptJob

    def fake_sj(**kw):
        if kw.get("listJobs"):
            return ['518: "conditionChange", "playingBack", <function _cb_playing_changed at 0x1>', '746: "event", "timeChanged", <function _cb_time_changed at 0x2>',
                    '900: "event", "SelectionChanged", <function _cb_selection_changed at 0x3>', '901: "event", "Undo", <function _cb_scene_event at 0x4>']
        if "kill" in kw:
            killed.append(kw["kill"])
        return None

    cmds.scriptJob = fake_sj
    try:
        n35 = T35b.purge_stale_jobs()
    finally:
        cmds.scriptJob = orig_sj
    check("AN-F-35: 古い tdrive_anim の scriptJob だけを掃除（他ツールの job は触らない）", killed == [518, 746, 901] and n35 == 3, str(killed))

    # ---- AN-F-37: 合成中はコントローラを隠す
    ctl_a = cmds.spaceLocator(name="c37_a_ctl")[0]
    ctl_b = cmds.spaceLocator(name="c37_b_ctl")[0]
    cmds.setAttr(ctl_b + ".visibility", lock=True)
    ctl_c = cmds.spaceLocator(name="c37_c_ctl")[0]
    cmds.sets(ctl_a, ctl_b, ctl_c, name="rig_controllers_grp")
    pls37 = Seq23.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}], clips23, 30)
    r37 = K23.compose("hide37", pls37, make_mini_rig.JOINTS, 30)
    check("AN-F-37: 合成中はコントローラ（rig_controllers_grp の中身）が非表示、ロック済みは触らない", not cmds.getAttr(ctl_a + ".visibility") and not cmds.getAttr(ctl_c + ".visibility") and cmds.getAttr(ctl_b + ".visibility"), "")
    check("AN-F-37: 非表示にした数が合成の情報に残る（バナー用）", K23.info("hide37").get("hiddenControllers") == 2, str(K23.info("hide37")))
    K23.suspend_for_save()
    check("AN-F-37: 保存の間は表示を戻す", cmds.getAttr(ctl_a + ".visibility") and cmds.getAttr(ctl_c + ".visibility"))
    K23.resume_after_save()
    check("AN-F-37: 保存のあとまた隠す", not cmds.getAttr(ctl_a + ".visibility"))
    K23.remove("hide37")
    check("AN-F-37: 外すと元の表示に戻る", cmds.getAttr(ctl_a + ".visibility") and cmds.getAttr(ctl_c + ".visibility"))
    K23.compose("keep37", pls37, make_mini_rig.JOINTS, 30, hide_ctls=False)
    check("AN-F-37: オプションをオフにすると隠さない", cmds.getAttr(ctl_a + ".visibility"))
    K23.remove("keep37")
    cmds.setAttr(ctl_a + ".visibility", 0)  # 元から隠していたものは、外しても隠したまま
    K23.compose("hide37", pls37, make_mini_rig.JOINTS, 30)
    K23.remove("hide37")
    check("AN-F-37: 元から非表示のものは外しても非表示のまま", not cmds.getAttr(ctl_a + ".visibility") and cmds.getAttr(ctl_c + ".visibility"))
    cmds.setAttr(ctl_b + ".visibility", lock=False)
    cmds.delete("rig_controllers_grp", ctl_a, ctl_b, ctl_c)

    # ---- AN-F-38: ショットタブのキャラクター表のクリップ列に要約
    from tdrive_anim.ui import AnimPanel as AP38

    panel38 = AP38()
    sh_tab = panel38.shot_tab
    s.new_shot("Sum38", 30, 0, 240)
    s.shot_add_character("MisotenChara", "", [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0},
                                              {"clip": "A_MisotenChara_Run_F", "holdSec": 1.5, "crossFadeSec": 0.08, "switchAt": 1.0, "speed": 1.0},
                                              {"clip": "A_MisotenChara_Jump", "holdSec": 0.0, "crossFadeSec": 0.08, "switchAt": 1.0, "speed": 1.0}])
    panel38.tabs.setCurrentWidget(sh_tab)
    sh_tab.refresh()
    cell = sh_tab.char_table.item(0, 2)
    check("AN-F-38: クリップ列のセルに要約が出る（Idle 1s → Run_F 1.5s → Jump）", cell is not None and cell.text() == "Idle 1s → Run_F 1.5s → Jump", cell.text() if cell else "")
    check("AN-F-38: ツールチップに全行", cell is not None and cell.toolTip().count("保持") == 3 and "ダブルクリック" in cell.toolTip(), cell.toolTip() if cell else "")
    dlg38 = sh_tab.show_sequence(0)
    tbl38 = dlg38.findChild(QtWidgets.QTableWidget)
    check("AN-F-38: ダブルクリックで読み取り専用の一覧（5 列・3 行）", tbl38.rowCount() == 3 and tbl38.columnCount() == 5 and tbl38.editTriggers() == QtWidgets.QAbstractItemView.NoEditTriggers and tbl38.item(1, 0).text() == "A_MisotenChara_Run_F", "")
    dlg38.close()
    panel38.detach()
    s.close_shot()

    # ---- AN-F-39: workspaceControl の「閉じる」（非表示）で合成・再生・scriptJob を外す
    from tdrive import shell as shell39
    from tdrive_anim import transport as T39
    from tdrive_anim.ui import AnimTool as AT39

    tool39 = AT39()
    widget39 = tool39.build_widget()
    shell_w = shell39.ShellWidget.__new__(shell39.ShellWidget)  # 殻の hide / show の知らせだけ試す（ツールの一覧を差し替える）
    QtWidgets.QWidget.__init__(shell_w)
    shell_w.tools = [tool39]
    shell_w.tool_widgets = {}
    shell_w.show()
    sj39 = []
    orig_sj39 = cmds.scriptJob
    cmds.scriptJob = lambda **kw: (sj39.append(kw), len(sj39))[1]
    try:
        p39 = tool39._panel
        p39.blend_tab.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0}]
        p39.blend_tab.build_from_rows()
        p39.naming_tab.inspect()
        SS.play({"shot": "Hide39", "frameRate": 30, "range": [0, 240], "cameras": []})
        check("AN-F-39: 前提: 合成中・再生中", K23.active() == ["blend"] and SS.is_playing() and p39.blend_tab._mix_job is not None)
        shell_w.hide()  # workspaceControl の「閉じる」相当
        check("AN-F-39: 非表示で合成が全部外れる", K23.active() == [] and not cmds.ls("tdAnimComp_*") and cmds.listConnections("spine_C0_0_jnt.translateX", source=True, destination=False) is not None)
        check("AN-F-39: 非表示で再生・トランスポートが止まる", not SS.is_playing() and not cmds.play(query=True, state=True) and T39.status() == [])
        check("AN-F-39: 非表示で scriptJob が外れ、タイマーが止まる", p39.blend_tab._mix_job is None and p39.naming_tab._jobs == [] and not p39._transport_timer.isActive() and not p39.clips_tab._range_timer.isActive())
        check("AN-F-39: 非表示で変更通知の購読も外れる", p39.refresh not in s.listeners)
        shell_w.show()  # 再表示
        check("AN-F-39: 再表示で使える状態に戻る（購読・タイマー・scriptJob）", p39.refresh in s.listeners and p39._transport_timer.isActive() and p39.clips_tab._range_timer.isActive() and bool(p39.naming_tab._jobs))
        p39.blend_tab.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0}]
        p39.blend_tab.build_from_rows()
        check("AN-F-39: 再表示のあとで合成を作れる", K23.active() == ["blend"])
        shell_w.hide()
        check("AN-F-39: もう一度閉じても外れる", K23.active() == [])
        shell_w.show()
        p39.detach()
    finally:
        cmds.scriptJob = orig_sj39
        shell_w.hide()
    check("AN-F-39: ツールに知らせの関数が無い殻（Toon / Facial）は何も起きない", not hasattr(object(), "on_hidden"))

    # ---- AN-F-40: 遷移列の表のコンボの上の右クリックでも行メニューが出る
    from PySide6 import QtCore as QC40

    from tdrive_anim.ui import AnimPanel as AP40

    panel40 = AP40()
    b40 = panel40.blend_tab
    b40.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0}]
    b40._fill_rows()
    combo40 = b40.rows_table.cellWidget(0, 0)
    shown40: list[list[str]] = []

    def grab_popup() -> None:  # 出たメニュー（モーダル）の中身を記録して閉じる
        pop = QtWidgets.QApplication.activePopupWidget()
        if pop is not None:
            shown40.append([x.text() for x in pop.actions()])
            pop.close()

    check("AN-F-40: コンボの右クリックが転送される設定", combo40.contextMenuPolicy() == QC40.Qt.CustomContextMenu)
    QC40.QTimer.singleShot(300, grab_popup)
    combo40.customContextMenuRequested.emit(QC40.QPoint(3, 3))
    check("AN-F-40: コンボ上の右クリックで行メニュー（コピー / 貼り付け / 行を消す）が出る", len(shown40) == 1 and "行を消す" in shown40[0] and "貼り付け" in shown40[0], str(shown40))
    check("AN-F-40: 右クリックした行が選ばれる", [i.row() for i in b40.rows_table.selectedIndexes()][:1] == [0])
    panel40.detach()

    # ---- AN-F-41: 出力の作業フォルダは失敗でも残らない・古いものは掃除
    import os as os41
    import tempfile as tf41
    import time as time41
    from pathlib import Path as P41

    from tdrive import export_job as EJ41

    old_dir = P41(tf41.mkdtemp(prefix="tdrive_anim_export_"))
    new_dir = P41(tf41.mkdtemp(prefix="tdrive_anim_export_"))
    other = P41(tf41.mkdtemp(prefix="unrelated_"))
    past = time41.time() - 3 * 24 * 3600
    os41.utime(old_dir, (past, past))
    os41.utime(other, (past, past))
    n41 = EJ41.purge_stale_workdirs()
    check("AN-F-41: 1 日より古い作業フォルダは掃除され、新しいものと関係ないフォルダは残る", n41 >= 1 and not old_dir.exists() and new_dir.exists() and other.exists(), f"{n41} {old_dir.exists()} {new_dir.exists()}")
    new_dir.rmdir()
    other.rmdir()
    job41 = EJ41.BatchJob.__new__(EJ41.BatchJob)  # 結果が書かれなかった（失敗）ジョブ
    job41.work = P41(tf41.mkdtemp(prefix="tdrive_anim_export_"))
    job41.result_path = job41.work / "result.json"
    job41.log = open(job41.work / "log.txt", "wb")
    job41.log.write("エラーの内容".encode("utf-8"))
    job41.log.flush()
    msg41 = ""
    try:
        job41.result()
    except RuntimeError as exc:
        msg41 = str(exc)
    check("AN-F-41: 失敗したジョブも作業フォルダを残さず、ログの内容は例外の文に入る", not job41.work.exists() and "エラーの内容" in msg41, msg41)

    # ---- AN-F-42: ▶ の直前の再生範囲・速度・ループに戻る（リロード / ■ / 範囲を変えて ▶ し直したとき）
    from tdrive import lifecycle as LC42
    from tdrive_anim import clips as MC42

    clip42 = {"name": "A_X_Clip", "start": 100, "end": 118, "loop": False}

    def set_state(mn, mx, loop, speed):
        cmds.playbackOptions(minTime=mn, maxTime=mx, animationStartTime=mn, animationEndTime=mx, loop=loop, playbackSpeed=speed, maxPlaybackSpeed=1)

    def get_state():
        return (cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True), cmds.playbackOptions(query=True, loop=True),
                cmds.playbackOptions(query=True, playbackSpeed=True))

    set_state(10, 200, "continuous", 1.0)
    MC42.play(clip42)
    MC42.stop()
    check("AN-F-42: ▶ → ■ で範囲 A（10〜200・連続・1 倍）に戻る", get_state() == (10, 200, "continuous", 1.0), str(get_state()))
    set_state(20, 150, "once", 0.5)  # 範囲 B・速度 0.5 に変えて ▶ → ■ を押さずにリロード
    MC42.play(clip42)
    check("AN-F-42: ▶ 中は clip の範囲", get_state()[:2] == (100, 118), str(get_state()))
    LC42.run_reload_cleanups()
    check("AN-F-42: ▶ のあとリロードしても、直前の範囲 B・once・速度 0.5 に戻る", get_state() == (20, 150, "once", 0.5), str(get_state()))
    # ▶ のあと ■ を押さずにユーザーが範囲を変えて、また ▶ → リロード
    set_state(10, 200, "continuous", 1.0)
    MC42.play(clip42)
    cmds.play(state=False)  # Maya 側で止めた（■ ではない）
    set_state(30, 90, "once", 0.5)  # ユーザーが範囲 C に変えた
    MC42.play(clip42)
    LC42.run_reload_cleanups()
    check("AN-F-42: 範囲を変えて ▶ し直してからリロードしても、直前の範囲 C に戻る（古い控えに戻らない）", get_state() == (30, 90, "once", 0.5), str(get_state()))
    MC42.forget()
    set_state(1, 120, "once", 1.0)
    MC42.stop()
    check("AN-F-42: ▶ していなければ何も変えない（控えが無いときは触らない）", get_state() == (1, 120, "once", 1.0), str(get_state()))

    # ---- AN-F-45: カメラを切り替えるパネルは見えているものを選ぶ
    from tdrive import panels as PN45

    models = ["modelPanel1", "modelPanel2", "modelPanel3", "modelPanel4"]
    check("AN-F-45: フォーカス無しでも、非表示の先頭（modelPanel1）でなく見えている modelPanel4 を選ぶ", PN45.pick_model_panel(models, ["modelPanel4"], "", "") == ("modelPanel4", True))
    check("AN-F-45: 見えているのが複数ならフォーカス → 最後にアクティブ → 先頭の順", PN45.pick_model_panel(models, ["modelPanel3", "modelPanel4"], "modelPanel4", "modelPanel3") == ("modelPanel4", True)
          and PN45.pick_model_panel(models, ["modelPanel3", "modelPanel4"], "", "modelPanel4") == ("modelPanel4", True)
          and PN45.pick_model_panel(models, ["modelPanel3", "modelPanel4"], "", "") == ("modelPanel3", True))
    check("AN-F-45: フォーカスが非表示のパネルなら使わない", PN45.pick_model_panel(models, ["modelPanel4"], "modelPanel1", "") == ("modelPanel4", True))
    check("AN-F-45: 見えているパネルが無ければ先頭（見えていない印つき）、パネルが無ければ None", PN45.pick_model_panel(models, [], "", "") == ("modelPanel1", False) and PN45.pick_model_panel([], [], "", "") == (None, False))
    saved45 = PN45.model_panel_info
    PN45.model_panel_info = lambda: ("modelPanel1", False)
    try:
        SS._model_panel = saved[0]
        SS._model_panel()
        check("AN-F-45: 選んだパネルが見えていなければ注意が出る", "表示されていません" in SS.panel_notice())
        PN45.model_panel_info = lambda: ("modelPanel4", True)
        SS._model_panel()
        check("AN-F-45: 見えていれば注意は出ない", SS.panel_notice() == "")
        binds45: list[str] = []
        saved_b = SS._bind_sequencer
        SS._bind_sequencer = lambda p: binds45.append(p)
        SS._reflected_camera["Rebind45"] = ("modelPanel4", "persp")
        SS._remember_panel_camera("Rebind45")
        SS._remember_panel_camera("Rebind45")
        SS._bind_sequencer = saved_b
        check("AN-F-45: 反映のたびに（2 回目以降も）見えているパネルを結び直す", binds45 == ["modelPanel4", "modelPanel4"], str(binds45))
        SS._reflected_camera.pop("Rebind45", None)
    finally:
        PN45.model_panel_info = saved45
    from tdrive_toon import environment as ENV45

    check("AN-F-45: Toon のプレビューも同じ選び方（tdrive.panels）", "panels" in ENV45.model_panel.__code__.co_names or ENV45.model_panel.__doc__ is not None)

    # ---- AN-F-17: メッセージ欄はスクロール領域の外・最上部
    from tdrive_anim.ui import AnimPanel
    from tdrive_anim.ui_common import MessageLabel

    panel = AnimPanel()
    lay = panel.layout()
    tabs = panel._all_tabs()
    outside = []
    for tab in tabs:
        w, in_scroll = tab.message, False
        p = w.parentWidget()
        while p is not None:
            in_scroll = in_scroll or isinstance(p, QtWidgets.QScrollArea)
            p = p.parentWidget()
        outside.append(isinstance(w, MessageLabel) and w.parentWidget() is panel.msg_host and not in_scroll)
    check("AN-F-17: 全タブ（5 つ）の message がスクロール領域の外・タブの外にある", len(tabs) == 5 and all(outside), str(outside))
    check("AN-F-17: メッセージ欄はタブより上", lay.indexOf(panel.msg_host) < lay.indexOf(panel.tabs))
    panel.tabs.setCurrentIndex(3)
    vis = [t.message.isHidden() for t in tabs]
    check("AN-F-17: 前に出ているタブの message だけ見せる", vis == [True, True, True, False, True], str(vis))
    panel.shot_tab.message.setText("⚠ 名前が不正です")
    check("AN-F-17: ⚠ はオレンジの背景で目立つ", "background-color" in panel.shot_tab.message.styleSheet())
    panel.shot_tab.message.setText("保存しました")
    check("AN-F-17: ふつうのメッセージは背景なし", "background-color" not in panel.shot_tab.message.styleSheet())
    panel.export_tab._append("⛔ 出せません")
    check("AN-F-17: 出力タブも message に最新行が出る", panel.export_tab.message.text() == "⛔ 出せません")
    panel.detach()
    # ---- AN-F-43 / AN-F-44
    from tdrive_anim.ui import AnimPanel as AP43

    if not s.data["clips"]:
        s.add_clip_from_range("Idle", 0, 30)
        s.add_clip_from_range("Run", 100, 118, direction="F")
    panel43 = AP43()  # リロード直後に相当: 前に出ているのは命名タブ。ブレンドタブへ移らなくても遷移列の表が描かれている
    check("AN-F-43: パネルを作った直後に、ブレンドタブの遷移列の表が描かれている", panel43.blend_tab.rows_table.rowCount() >= 1, f"{panel43.blend_tab.rows_table.rowCount()} rows={panel43.blend_tab.rows} mode={panel43.blend_tab.mode} msg={panel43.blend_tab.message.text()} model={s.data['modelId']}")
    b43 = panel43.blend_tab
    b43.use_graph()
    b43.timeline = [(1.0, {"Speed": 0.5})]
    b43._total_manual = True
    b43._fill_graph_info()
    check("AN-F-44: 前提: 状態図・記録・タイムラインに内容がある", b43.graph is not None and b43.timeline_view.markers and b43.mode == "graph")
    s.close_animset()
    check("AN-F-44: animset を閉じると状態図の節が初期表示（状態図・記録・タイムライン・長さ）",
          b43.graph is None and b43.timeline == [] and b43.timeline_view.markers == [] and b43.mode == "rows" and b43.timeline_table.rowCount() == 0
          and abs(b43.total_sec.value() - 8.0) < 1e-9 and "スライダーを動かすか" in b43.graph_info.text(), b43.graph_info.text())
    panel43.detach()

    for t in (clips_tab, blend):
        if t.refresh in s.listeners:  # リロードの後片付け（AN-F-35 の検査）で外れていることがある
            s.listeners.remove(t.refresh)


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
