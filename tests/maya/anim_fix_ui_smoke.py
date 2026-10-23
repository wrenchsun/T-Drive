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
    blend.remove()
    check("AN-F-5: 合成を外すと表示が消える", blend.mix_label.text() == "")
    # ---- AN-F-15: シーケンサー再生でパネルのカメラが切り替わる
    from tdrive_anim import shot_scene as SS

    cam_a = cmds.camera(name="CAM_Main")[0]
    cam_b = cmds.camera(name="CAM_Close")[0]
    cmds.camera(name="CAM_Orig")
    shot = {"shot": "Opening01", "range": [0, 240], "cameras": [{"name": "CAM_Main", "node": cam_a, "start": 0, "end": 120},
                                                                  {"name": "CAM_Close", "node": cam_b, "start": 121, "end": 240}]}
    looks: list[str] = []
    saved = (SS._model_panel, SS._panel_camera, SS._look_thru)
    SS._model_panel, SS._panel_camera, SS._look_thru = (lambda: "modelPanel4", lambda p: "CAM_Orig1", lambda p, c: looks.append(c))
    try:
        following = SS.play(shot)
        check("AN-F-15: 再生を始めると最初のカメラ（CAM_Main）に切り替わる", following and looks == [cam_a], str(looks))
        cmds.currentTime(50, edit=True)
        SS.follow_frame()
        check("AN-F-15: 区間の中では切り替え直さない", looks == [cam_a], str(looks))
        cmds.currentTime(130, edit=True)
        SS.follow_frame()
        check("AN-F-15: 121 フレーム以降は CAM_Close に切り替わる", looks == [cam_a, cam_b], str(looks))
        SS.stop()
        check("AN-F-15: 止めたら元のカメラに戻る", looks[-1] == "CAM_Orig1" and SS._follow["shot"] is None, str(looks))
    finally:
        SS._model_panel, SS._panel_camera, SS._look_thru = saved
    SS._model_panel = lambda: None
    check("AN-F-15: パネルが無い環境では追従しない", SS.start_follow(shot) is False)
    SS._model_panel = saved[0]

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
    for t in (clips_tab, blend):
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
