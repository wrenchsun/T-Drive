"""外部ツールの読み込み（tdrive.external_load → shell.register_tool）のスモーク（mayapy・画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/external_load_smoke.py

tools.json は一時フォルダに書き、dir に tests/fixtures の外部ツール（ダミー / create_tool が例外）を絶対パスで指す。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))

from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_extload_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    from tdrive import external_load, shell

    fx = REPO / "tests" / "fixtures"
    tools_json = tmp / "tools.json"
    tools_json.write_text(
        json.dumps({"version": 1, "tools": [
            {"id": "tdrive_broken", "dir": str(fx / "external_tool_broken")},
            {"id": "tdrive_dummy", "dir": str(fx / "external_tool")},
            {"id": "tdrive_off", "dir": str(fx / "external_tool"), "enabled": False},
        ]}),
        encoding="utf-8",
    )
    ids = external_load.load_external_tools(tools_json)
    tools = shell.tools()
    order = [t.id for t in tools]
    check("読み込んだ id", ids == ["tdrive_broken", "tdrive_dummy"], str(ids))
    check("組み込みの後ろに外部が並ぶ", order[:3] == ["toon", "facial", "anim"] and order[3:] == ["tdrive_broken", "tdrive_dummy"], str(order))
    dummy = shell.get_tool("tdrive_dummy")
    check("ダミーは本物のツール", dummy is not None and not isinstance(dummy, shell._UnavailableTool) and dummy.label == "Dummy")
    broken = shell.get_tool("tdrive_broken")
    check("壊れたものは _UnavailableTool", isinstance(broken, shell._UnavailableTool))
    check("無効のものはタブが無い", shell.get_tool("tdrive_off") is None)
    check("Toon / FacialController は生きている", not isinstance(shell.get_tool("toon"), shell._UnavailableTool) and not isinstance(shell.get_tool("facial"), shell._UnavailableTool))

    widget = shell.ShellWidget()
    labels = [widget.tool_tabs.tabText(i) for i in range(widget.tool_tabs.count())]
    check("1 段目のタブに並ぶ", labels[-2:] == ["Broken", "Dummy"] and "Toon" in labels, str(labels))
    widget.detach()

    external_load.unload()
    check("unload でモジュールを外す", "tdrive_dummy.tool" not in sys.modules and str(fx / "external_tool" / "maya" / "scripts") not in sys.path)


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
