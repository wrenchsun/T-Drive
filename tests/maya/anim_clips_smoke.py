"""クリップタブのスモーク（AN-3。mayapy・ヘッドレス。dx11 / Render Override は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_clips_smoke.py

確かめること: クリップを 3 つ作る → 再生範囲の出し入れ（元に戻る）→ 保存 → 開き直しで戻る・重複名はエラー・
イベント・Undo・UI（一覧・検索・編集・属性の貼り付け）・ビューポートが無い環境ではサムネイルを作れない理由を返す。
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

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_clips_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import clips as MC
    from tdrive_anim import session as S
    from tdrive_anim import ui_clips
    from tdrive_anim.core import animset as A
    from tdrive_anim.core import clips as C

    project.set_root(tmp)
    make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    make_mini_rig.key_motion(0, 60)
    s = S.current()
    s.new("MisotenChara")

    check("ルート以下のキーの範囲が分かる", MC.keyed_range("spine_C0_0_jnt") == (0, 60), str(MC.keyed_range("spine_C0_0_jnt")))

    # ---- 範囲から 3 つ作る
    cmds.playbackOptions(minTime=0, maxTime=60)
    s.add_clip_from_range("Idle", *MC.playback_range())
    cmds.playbackOptions(minTime=100, maxTime=118)
    s.add_clip_from_range("Run", *MC.playback_range(), direction="F")
    cmds.playbackOptions(minTime=200, maxTime=230)
    s.add_clip_from_range("Jump", *MC.playback_range())
    names = [c["name"] for c in s.data["clips"]]
    check("クリップを 3 つ作る", names == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F", "A_MisotenChara_Jump"], str(names))
    try:
        s.add_clip_from_range("Jump", 1, 2)
        check("重複名はエラー", False)
    except A.AnimSetError:
        check("重複名はエラー", len(s.data["clips"]) == 3)
    check("Undo で 1 つ戻る", s.undo() is True and len(s.data["clips"]) == 2 and s.redo() and len(s.data["clips"]) == 3)

    # ---- 再生: 元の範囲・時刻・ループに戻る
    cmds.playbackOptions(minTime=5, maxTime=50, animationStartTime=0, animationEndTime=100, loop="once")
    cmds.currentTime(12, edit=True)
    run_clip = A.find_clip(s.data, "A_MisotenChara_Run_F")
    MC.play(run_clip)
    check("再生中は範囲がクリップの範囲", MC.playback_range() == (100, 118) and MC.is_playing())
    check("ループ ON のクリップは continuous", cmds.playbackOptions(query=True, loop=True) == "continuous")
    MC.play(A.find_clip(s.data, "A_MisotenChara_Jump"))
    check("再生中に別のクリップへ切り替えても元の状態は 1 つだけ保存", MC.playback_range() == (200, 230))
    MC.stop()
    check(
        "止めると元の再生範囲・ループ・時刻に戻る",
        MC.playback_range() == (5, 50) and cmds.playbackOptions(query=True, animationEndTime=True) == 100
        and cmds.playbackOptions(query=True, loop=True) == "once" and cmds.currentTime(query=True) == 12 and not MC.is_playing(),
    )
    MC.stop()  # 二重に止めても落ちない
    MC.set_slider_range(100, 118)
    check("範囲をタイムスライダーに", MC.playback_range() == (100, 118))

    # ---- イベント・属性
    cmds.currentTime(104, edit=True)
    s.add_foot_plant("A_MisotenChara_Run_F", int(cmds.currentTime(query=True)), "left")
    s.add_event("A_MisotenChara_Run_F", 110, "Hit")
    ev = A.find_clip(s.data, "A_MisotenChara_Run_F")["events"]
    check("イベントが入る（足音は左 = 0）", [(e["frame"], e["key"], e.get("args")) for e in ev] == [(104, "OnFootPlant", 0), (110, "Hit", None)], str(ev))
    try:
        s.add_event("A_MisotenChara_Run_F", 5, "Out")
        check("範囲外のイベントはエラー", False)
    except A.AnimSetError:
        check("範囲外のイベントはエラー", True)
    s.update_clip("A_MisotenChara_Run_F", crossFade=0.08)
    check("属性を変えられる", A.find_clip(s.data, "A_MisotenChara_Run_F")["crossFade"] == 0.08)
    s.undo()
    check("属性の変更も 1 Undo", A.find_clip(s.data, "A_MisotenChara_Run_F")["crossFade"] == 0.1)

    # ---- 保存 → 開き直しで戻る
    path = s.save()
    before = A.load(path)
    s.data = None
    S.on_scene_opened()
    check("保存・開き直しでクリップが戻る", s.data == before and len(s.data["clips"]) == 3 and len(A.find_clip(s.data, "A_MisotenChara_Run_F")["events"]) == 2)
    check("検証にエラーがない", A.errors(s.data) == [])

    # ---- サムネイル: ビューポートが無い環境では理由つきで作らない
    p, why = MC.make_thumbnail("MisotenChara", s.data["clips"][0])
    check("mayapy ではサムネイルを作れない理由を返す（例外にしない）", p is None and "サムネイル" in why, why)
    check("サムネイルの鍵は範囲で変わる", MC.thumb_path("M", {"name": "a", "start": 0, "end": 1}) != MC.thumb_path("M", {"name": "a", "start": 0, "end": 2}))

    # ---- UI
    ui = ui_clips.ClipsTab(s)
    s.listeners.append(ui.refresh)
    ui.refresh()
    APP.processEvents()
    check("UI: 3 行出る", ui.table.rowCount() == 3, str(ui.table.rowCount()))
    check("UI: 動作の語彙がコンボに入る", ui.action.count() >= 10)
    ui.search.setText("run")
    check("UI: 検索で絞り込む", ui.table.rowCount() == 1 and ui._clip_names == ["A_MisotenChara_Run_F"])
    ui.search.setText("OnFootPlant")
    check("UI: イベントのキーで検索できる", ui._clip_names == ["A_MisotenChara_Run_F"])
    ui.search.setText("")
    ui.table.selectRow(0)
    APP.processEvents()
    check("UI: 選ぶと詳細が出る", ui.root_motion.isEnabled() and ui.root_motion.currentText() == "none")
    ui.table.item(0, ui_clips.COL_END).setText("70")
    check("UI: 終了フレームを編集できる", A.find_clip(s.data, "A_MisotenChara_Idle")["end"] == 70)
    ui.table.item(0, ui_clips.COL_END).setText("abc")
    check("UI: 整数でない入力は警告だけで元に戻る", "整数" in ui.message.text() and A.find_clip(s.data, "A_MisotenChara_Idle")["end"] == 70, ui.message.text())
    ui.table.item(0, ui_clips.COL_END).setText("-3")
    check("UI: 逆転した範囲は警告だけで元に戻る", A.find_clip(s.data, "A_MisotenChara_Idle")["end"] == 70 and "⚠" in ui.message.text(), ui.message.text())
    ui.table.item(0, ui_clips.COL_NAME).setText("A_MisotenChara_Jump")
    check("UI: 重複名への改名はエラー表示で元に戻る", "同じ名前" in ui.message.text() and A.find_clip(s.data, "A_MisotenChara_Idle") is not None, ui.message.text())
    ui.table.item(0, ui_clips.COL_NAME).setText("Idle2")
    check("UI: 規則に合わない名前でも改名はできるが❌が出る", A.find_clip(s.data, "Idle2") is not None and "❌" in ui.message.text(), ui.message.text())
    ui.table.item(0, ui_clips.COL_NAME).setText("A_MisotenChara_Idle")
    ui.table.selectRow(1)
    ui.ev_key.setText("Wave")
    cmds.currentTime(105, edit=True)
    ui.add_event()
    check("UI: 現在フレームにイベントを追加", any(e["key"] == "Wave" and e["frame"] == 105 for e in A.find_clip(s.data, "A_MisotenChara_Run_F")["events"]))
    ui.add_foot("right")
    check("UI: 足音 右", any(e["key"] == "OnFootPlant" and e.get("args") == 1 for e in A.find_clip(s.data, "A_MisotenChara_Run_F")["events"]))
    ui.cross.setValue(0.08)
    ui.cross.editingFinished.emit()
    check("UI: クロスフェードを変えられる", A.find_clip(s.data, "A_MisotenChara_Run_F")["crossFade"] == 0.08)
    QtWidgets.QApplication.clipboard().setText(C.attrs_to_text(A.find_clip(s.data, "A_MisotenChara_Run_F")))
    ui.table.selectRow(2)
    ui._guard(s.paste_attrs, ui.selected_names(), QtWidgets.QApplication.clipboard().text())
    check("UI: 属性の貼り付け", A.find_clip(s.data, "A_MisotenChara_Jump")["crossFade"] == 0.08)
    ui._guard(s.paste_attrs, ui.selected_names(), "ぜんぜん違う文字列")
    check("UI: 別の文字列は貼り付けられず警告", "貼り付けられません" in ui.message.text(), ui.message.text())
    ui.add_from_range()
    check("UI: 現在の範囲から追加", len(s.data["clips"]) == 4 or "同じ名前" in ui.message.text(), ui.message.text())
    ui.table.selectRow(0)
    ui.play_selected()
    check("UI: 再生中になる", MC.is_playing())
    s.listeners.remove(ui.refresh)
    ui.detach()
    check("UI: detach で再生範囲を戻す", not MC.is_playing())
    s.new("MisotenChara")
    ui.refresh()
    check("UI: 空の animset でも落ちない", ui.table.rowCount() == 0)


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
