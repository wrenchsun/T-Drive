"""Phase AN コードレビュー（docs/21）の命名・クリップタブまわりの回帰スモーク（mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_review_ui_smoke.py

UX-5 まとめて削除・複製・一括変更・改名は 1 回の Undo + 削除の確認 / SF-11 Look ファイルの書き換えを先に見せて確認 /
UX-13 詳細欄にループ / UX-14 範囲の表示がタイムスライダーに追従。
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
ANSWER = {"value": QtWidgets.QMessageBox.Yes, "asked": []}


def _question(parent, title, text, *a, **k):
    ANSWER["asked"].append(text)
    return ANSWER["value"]


QtWidgets.QMessageBox.question = staticmethod(_question)

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_review_ui_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    import json

    from maya import cmds
    from PySide6 import QtCore

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import session as S
    from tdrive_anim import ui_clips, ui_naming
    from tdrive_anim.core import animset as A
    from tdrive_toon import look as look_mod
    from tdrive_toon import session as toon

    project.set_root(tmp)
    rig = make_mini_rig.build()
    make_mini_rig.add_materials()
    cmds.currentUnit(time="ntsc")
    s = S.current()
    s.new("MisotenChara", meshes=[rig["mesh"]])
    for n, a, b in (("Idle", 0, 30), ("Run", 100, 118), ("Jump", 200, 230), ("Wave", 300, 330)):
        s.add_clip_from_range(n, a, b, direction="F" if n == "Run" else "")

    # ---- UX-5: セッション層のまとめ操作は 1 つの Undo
    def undo_steps(fn):
        n0 = len(s._undo)
        fn()
        return len(s._undo) - n0

    names = [c["name"] for c in s.data["clips"]]
    check("UX-5: update_clips（一括変更）は 1 回の Undo", undo_steps(lambda: s.update_clips(names[:3], rootMotion="bake")) == 1
          and all(A.find_clip(s.data, n)["rootMotion"] == "bake" for n in names[:3]))
    s.undo()
    check("UX-5: 1 回の Ctrl+Z で一括変更が全部戻る", all(A.find_clip(s.data, n)["rootMotion"] == "none" for n in names))
    try:
        s.update_clips(names[:2], crossFade=-1)
        check("UX-5: 不正な値は例外", False)
    except A.AnimSetError:
        check("UX-5: 不正な値は例外で、全部元のまま", all(A.find_clip(s.data, n)["crossFade"] >= 0 for n in names))
    check("UX-5: duplicate_clips は 1 回の Undo", undo_steps(lambda: s.duplicate_clips(names[:3])) == 1 and len(s.data["clips"]) == 7)
    s.undo()
    check("UX-5: 複製は 1 回の Ctrl+Z で全部戻る", len(s.data["clips"]) == 4)
    check("UX-5: remove_clips は 1 回の Undo", undo_steps(lambda: s.remove_clips(names[:3])) == 1 and len(s.data["clips"]) == 1)
    s.undo()
    check("UX-5: 削除は 1 回の Ctrl+Z で全部戻る", len(s.data["clips"]) == 4)
    pairs = [(names[0], "A_MisotenChara_Idle2"), (names[1], "A_MisotenChara_Run2"), ("A_Nope", "A_MisotenChara_X")]
    n0 = len(s._undo)
    done = s.rename_clips(pairs)
    check("UX-5: rename_clips は 1 回の Undo で、できないものは飛ばす", len(done) == 2 and len(s._undo) == n0 + 1, str(done))
    s.undo()
    check("UX-5: 改名は 1 回の Ctrl+Z で全部戻る", [c["name"] for c in s.data["clips"]] == names)
    A.find_clip(s.data, names[0])["events"] = [{"frame": 1, "key": "a"}, {"frame": 2, "key": "b"}, {"frame": 3, "key": "c"}]
    n0 = len(s._undo)
    got = s.remove_events(names[0], [0, 2])
    check("UX-5: remove_events は 1 回の Undo", got == 2 and len(s._undo) == n0 + 1 and [e["key"] for e in A.find_clip(s.data, names[0])["events"]] == ["b"])

    # ---- UX-5: クリップタブ（確認 + 1 Undo）。UX-13 ループ / UX-14 範囲の表示
    tab = ui_clips.ClipsTab(s)
    s.listeners.append(tab.refresh)
    tab.show()
    tab.refresh()
    tab.table.selectAll()
    ANSWER["asked"].clear()
    ANSWER["value"] = QtWidgets.QMessageBox.No
    tab.delete_selected()
    check("UX-5: 削除は確認が出て、「いいえ」なら消えない", len(s.data["clips"]) == 4 and ANSWER["asked"] and "削除" in ANSWER["asked"][0])
    ANSWER["value"] = QtWidgets.QMessageBox.Yes
    n0 = len(s._undo)
    tab.delete_selected()
    check("UX-5: 「はい」なら選んだ全部が消え、Undo は 1 回", len(s.data["clips"]) == 0 and len(s._undo) == n0 + 1)
    s.undo()
    tab.refresh()
    tab.table.selectRow(0)
    tab.table.selectAll()
    n0 = len(s._undo)
    tab._apply_detail("rootMotion", "bake")
    check("UX-5: 複数選択の詳細の変更は全体で 1 回の Undo", len(s._undo) == n0 + 1 and all(c["rootMotion"] == "bake" for c in s.data["clips"]))
    s.undo()
    tab.refresh()
    tab.table.selectAll()
    n0 = len(s._undo)
    tab.duplicate_selected()
    check("UX-5: 複数選択の複製は 1 回の Undo", len(s.data["clips"]) == 8 and len(s._undo) == n0 + 1)
    s.undo()
    tab.refresh()
    tab.table.selectRow(0)
    first = A.find_clip(s.data, tab.selected_names()[0])
    loop_before = bool(first["loop"])
    tab.loop_cb.click()  # clicked シグナル（ユーザー操作）で反映
    check("UX-13: 詳細欄にループの項目があり、操作が反映される", A.find_clip(s.data, first["name"])["loop"] != loop_before)
    now_loop = A.find_clip(s.data, first["name"])["loop"]
    tab._fill_detail()
    check("UX-13: 詳細欄のループが選んだクリップの値を映す", tab.loop_cb.isChecked() == now_loop)
    cmds.playbackOptions(minTime=3, maxTime=77)
    tab._update_range_label()
    check("UX-14: 範囲の表示がタイムスライダーに追従する（タブが前面のあいだ）", "3〜77" in tab.range_label.text(), tab.range_label.text())
    tab.detach()
    s.listeners.remove(tab.refresh)

    # ---- SF-11: Look ファイルの書き換えは、先に見せて確認する
    lk = look_mod.new_look("miso", "MisotenChara")
    look_mod.register_part(lk, "hair", "hair", ["Hair"])
    path = tmp / "looks" / "miso" / "look.json"
    look_mod.save(lk, path)
    toon.current().look = None
    before = path.read_text(encoding="utf-8")
    nt = ui_naming.NamingTab(s)
    s.listeners.append(nt.refresh)
    nt.inspect()
    pick = [r for r, f in enumerate(nt.report.findings) if f.kind == "material" and f.name == "Hair"]
    for r in range(nt.table.rowCount()):
        it = nt.table.item(r, ui_naming.COL_APPLY)
        it.setCheckState(QtCore.Qt.Checked if r in pick else QtCore.Qt.Unchecked)
    ANSWER["asked"].clear()
    ANSWER["value"] = QtWidgets.QMessageBox.No
    nt.apply()
    asked = " ".join(ANSWER["asked"])
    check("SF-11: 書き換える look.json を先に見せて確認する（ファイル名と箇所数）", "looks/miso/look.json" in asked.replace("\\", "/") and "か所" in asked and "戻せません" in asked, asked)
    check("SF-11: 「いいえ」なら look.json は書き換わらず、マテリアルの改名だけ行う", path.read_text(encoding="utf-8") == before and cmds.objExists("M_MisotenChara_Hair"))
    check("SF-11: 書き換えなかった Look は、旧い名前のままと知らせる", "旧い名前のまま" in nt.result.text(), nt.result.text())
    cmds.undo()
    nt.inspect()
    for r in range(nt.table.rowCount()):
        nt.table.item(r, ui_naming.COL_APPLY).setCheckState(QtCore.Qt.Checked if nt.report.findings[r].name == "Hair" and nt.report.findings[r].kind == "material" else QtCore.Qt.Unchecked)
    ANSWER["value"] = QtWidgets.QMessageBox.Yes
    nt.apply()
    saved = json.loads(path.read_text(encoding="utf-8"))
    check("SF-11: 「はい」なら look.json が書き換わり、書き換えたことが Ctrl+Z で戻らないと出る", "M_MisotenChara_Hair" in saved["materials"] and "戻りません" in nt.result.text(), nt.result.text())
    # 掃除は確認
    ANSWER["asked"].clear()
    ANSWER["value"] = QtWidgets.QMessageBox.No
    for i in range(nt.cleanup_list.count()):
        nt.cleanup_list.item(i).setCheckState(QtCore.Qt.Checked)
    n_before = nt.cleanup_list.count()
    nt.cleanup()
    check("UX-5: 掃除の削除は確認が出て、「いいえ」なら消えない", n_before > 0 and ANSWER["asked"] and cmds.objExists("Unused"), str(ANSWER["asked"]))
    ANSWER["value"] = QtWidgets.QMessageBox.Yes
    nt.cleanup()
    check("UX-5: 「はい」なら消える", not cmds.objExists("Unused"))
    nt.detach()
    s.listeners.remove(nt.refresh)


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
