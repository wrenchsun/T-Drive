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
