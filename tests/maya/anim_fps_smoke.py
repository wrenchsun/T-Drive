"""fps を「直す」ときのクリップ範囲の追従のスモーク（AN-F-13。mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_fps_smoke.py

24 fps（film）のシーンで 0〜60 のクリップを 30 fps に「直す」と、キーの時刻が保たれてフレーム番号が 1.25 倍になり、
クリップの範囲・イベント・ショットの範囲も 1.25 倍に直る（中身の動きが同じ）。「フレーム番号を保つ」を選べば範囲はそのまま。
範囲にキーの無いクリップはクリップタブに⚠が出る。
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
ANSWER = {"value": QtWidgets.QMessageBox.Yes}
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: ANSWER["value"])

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_fps_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def setup(tmp):
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import session as S

    project.set_root(tmp)
    make_mini_rig.build()  # film（24 fps）
    for f in (0, 20, 40, 60):
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=f * 2.0)
    s = S.current()
    s.new("MisotenChara")
    s.add_clip_from_range("Idle", 0, 60)
    cmds.playbackOptions(minTime=0, maxTime=60)
    s.add_event("A_MisotenChara_Idle", 30, "Hit")
    return s


def _run(tmp: Path) -> None:
    from maya import cmds

    from tdrive_anim import scene_naming as SN
    from tdrive_anim import ui_clips, ui_naming

    s = setup(tmp)
    s.new_shot("Opening01", 30, 0, 120)
    s.shot_add_event(60, "Beat")
    clip = s.data["clips"][0]
    before = {f: cmds.getAttr("spine_C0_0_jnt.translateX", time=f) for f in (0, 12, 30, 45, 60)}
    nt = ui_naming.NamingTab(s)
    nt.inspect()
    ANSWER["value"] = QtWidgets.QMessageBox.Cancel
    nt.fix_setting("time")
    check("キャンセルなら何も変わらない", cmds.currentUnit(query=True, time=True) == "film" and (clip["start"], clip["end"]) == (0, 60))
    ANSWER["value"] = QtWidgets.QMessageBox.Yes
    nt.fix_setting("time")
    clip = s.data["clips"][0]
    check("30 fps になり、クリップ範囲が 0〜60 → 0〜75", cmds.currentUnit(query=True, time=True) == "ntsc" and (clip["start"], clip["end"]) == (0, 75), str(clip))
    check("イベントのフレームも 30 → 38（1.25 倍を四捨五入）", clip["events"][0]["frame"] == 38, str(clip["events"]))
    check("ショットの範囲・イベントも 1.25 倍", s.shot["range"] == [0, 150] and s.shot["events"][0]["frame"] == 75, str(s.shot))
    after = {f: cmds.getAttr("spine_C0_0_jnt.translateX", time=f * 1.25) for f in before}
    check("範囲の中身の動きが同じ（元の f フレームの値 = 新しい 1.25 f フレームの値）", all(abs(after[f] - before[f]) < 1e-4 for f in before), f"{before} {after}")
    check("範囲にキーが無いクリップは無い", SN.clips_without_keys(s.data) == [])
    check("結果の欄に直したことが出る", "1.25" in nt.result.text(), nt.result.text())
    s.undo()
    check("ツールの Undo でクリップ範囲が戻る", (s.data["clips"][0]["start"], s.data["clips"][0]["end"]) == (0, 60) and s.shot["range"] == [0, 120])

    # フレーム番号を保つ
    s = setup(tmp)
    nt = ui_naming.NamingTab(s)
    nt.inspect()
    ANSWER["value"] = QtWidgets.QMessageBox.No
    nt.fix_setting("time")
    clip = s.data["clips"][0]
    check("「フレーム番号を保つ」はクリップ範囲をそのままに、キーのフレーム番号も保つ", (clip["start"], clip["end"]) == (0, 60)
          and cmds.currentUnit(query=True, time=True) == "ntsc" and abs(cmds.getAttr("spine_C0_0_jnt.translateX", time=30) - 60.0) < 1e-4)

    # ⚠ の表示
    tab = ui_clips.ClipsTab(s)
    tab.refresh()
    check("範囲にキーのあるクリップは⚠が出ない", tab.nokey_label.text() == "")
    s.add_clip_from_range("Run", 300, 330, direction="F")
    tab.refresh()
    check("範囲にキーの無いクリップに⚠", "A_MisotenChara_Run_F" in tab.nokey_label.text() and "Idle" not in tab.nokey_label.text(), tab.nokey_label.text())


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
