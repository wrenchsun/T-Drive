"""更新ウィンドウの「外部ツール」区画（tdrive.ui_update.UpdateWindow）のスモーク（mayapy・画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/external_update_smoke.py

bare リポジトリ（GitHub 役）にダミーツール（tests/fixtures/external_tool）を push して v0.1.0 / v0.2.0 を打ち、
ウィジェットのボタン経由で 追加 → 一覧 → 最新の版を確認 → 更新 → 元に戻す → 無効 / 有効 → 削除 を通す（ダイアログは関数を差し替える）。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
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

CHANGELOG = """# tdrive_dummy

## [Unreleased]

## [0.2.0] - 2026-10-14

### 変更
- 機能を足した

### 互換性
- 追加のみ

## [0.1.0] - 2026-10-01

### 追加
- 初回
"""


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def _git(cwd, *args) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _rmtree(path: Path) -> None:
    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=onerror)


def _make_remote(tmp: Path) -> str:
    work, bare = tmp / "work", tmp / "remote.git"
    shutil.copytree(REPO / "tests" / "fixtures" / "external_tool", work)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    for ver in ("0.1.0", "0.2.0"):
        (work / "VERSION").write_text(ver + "\n", encoding="utf-8")
        (work / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", ver)
        _git(work, "tag", "-a", f"v{ver}", "-m", ver)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare.as_posix()


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_extupd_"))
    os.environ["LOCALAPPDATA"] = str(tmp / "local")  # 本物の tools.json に触れない
    try:
        _run(tmp)
    finally:
        _rmtree(tmp)


def _row_texts(win) -> list[str]:
    return [win.ext_table.item(0, c).text() for c in range(win.ext_table.columnCount())] if win.ext_table.rowCount() else []


def _run(tmp: Path) -> None:
    from tdrive import external, external_load, shell, ui_update, updater

    url = _make_remote(tmp)
    base = tmp / "TDriveToon"
    win = ui_update.UpdateWindow()
    win.ext_base = base
    infos, warns, asked = [], [], []
    answers = {"url": (url, "v0.1.0"), "confirm": True}
    win._ext_ask_url = lambda: (asked.append(1), answers["url"])[1]
    win._ext_confirm = lambda text: (infos.append("confirm: " + text), answers["confirm"])[1]
    win._ext_warn = warns.append
    win._ext_info = infos.append
    win.ext_refresh()
    check("最初は一覧が空", win.ext_table.rowCount() == 0 and not win.ext_update_btn.isEnabled() and not win.ext_remove_btn.isEnabled())

    # ---- 失敗は追加しない（本体と同じ警告）
    answers["url"] = ((tmp / "nothing.git").as_posix(), "")
    win.ext_add_btn.click()
    check("clone できない URL は警告して追加しない", len(warns) == 1 and win.ext_table.rowCount() == 0 and external.load_tools(external.tools_json_path(base))[0] == [], str(warns))
    answers["url"] = None  # キャンセル
    win.ext_add_btn.click()
    check("キャンセルは何もしない", len(warns) == 1 and win.ext_table.rowCount() == 0)

    # ---- 追加（v0.1.0 に固定）→ その場で読み込み
    warns.clear()
    answers["url"] = (url, "v0.1.0")
    win.ext_add_btn.click()
    row = _row_texts(win)
    check("追加で一覧に出る", row[:3] == ["Dummy", "0.1.0", "v0.1.0"] and row[5] == "有効", str(row))
    tool = shell.get_tool("tdrive_dummy")
    check("その場で読み込まれる（本物のタブ）", tool is not None and not isinstance(tool, shell._UnavailableTool))
    check("追加直後は最新の版が未確認", row[3] == "-" and not warns, str(warns))

    # ---- 最新の版を確認
    win.ext_check_btn.click()
    row = _row_texts(win)
    check("最新の版 v0.2.0 / MINOR", row[3] == "v0.2.0" and row[4] == "MINOR", str(row))
    color = win.ext_table.item(0, 4).foreground().color().name()
    check("MINOR は本体と同じ色", color == ui_update.KIND_COLOR["minor"], color)
    check("変更点が本体と同じ欄に出る", "機能を足した" in win.changes.toPlainText(), win.changes.toPlainText())

    # ---- 更新
    infos.clear()
    win.ext_update_btn.click()
    row = _row_texts(win)
    check("更新で v0.2.0 になる", row[1] == "0.2.0" and row[2] == "v0.2.0" and row[4] == "最新", str(row))
    check("更新後はリロードを案内", any("ツールをリロード" in t for t in infos), str(infos))
    saved = external.load_tools(external.tools_json_path(base))[0][0]
    check("tools.json の tag / previous", (saved.tag, saved.previous) == ("v0.2.0", "v0.1.0"), str(saved))

    # ---- 元に戻す
    win.ext_rollback_btn.click()
    row = _row_texts(win)
    check("元に戻すで v0.1.0", row[1] == "0.1.0" and row[2] == "v0.1.0", str(row))
    check("元に戻したあとの上げ幅 MINOR（最新は v0.2.0 のまま）", row[4] == "MINOR", str(row))

    # ---- 無効 / 有効
    win.ext_enable_btn.click()
    check("無効にできる", _row_texts(win)[5] == "無効" and win.ext_enable_btn.text() == "有効にする" and (base / "tools" / "tdrive_dummy").is_dir())
    win.ext_enable_btn.click()
    check("有効に戻せる", _row_texts(win)[5] == "有効" and win.ext_enable_btn.text() == "無効にする")

    # ---- 削除
    answers["confirm"] = False
    win.ext_remove_btn.click()
    check("確認で「いいえ」なら消さない", win.ext_table.rowCount() == 1 and (base / "tools" / "tdrive_dummy").is_dir())
    answers["confirm"] = True
    win.ext_remove_btn.click()
    check("削除で一覧と checkout が消える", win.ext_table.rowCount() == 0 and not (base / "tools" / "tdrive_dummy").exists())
    check("tools.json からも外れる", external.load_tools(external.tools_json_path(base))[0] == [])
    check("本体の区画は生きている", win.check_btn.text() == "最新の版を確認" and win.update_btn.text() == "この版に更新" and not warns, str(warns))

    # ---- 開発用（絶対パスの clone）は更新・元に戻すが無効
    mine = tmp / "mine"
    subprocess.run(["git", "clone", "-q", url, str(mine)], check=True)
    external.upsert_tool(external.ToolEntry(id="tdrive_dummy", url=url, dir=str(mine)), external.tools_json_path(base))
    win.ext_refresh()
    check("開発用は更新・元に戻すが無効（理由つき）", not win.ext_update_btn.isEnabled() and not win.ext_rollback_btn.isEnabled() and "開発用" in win.ext_update_btn.toolTip() and "開発用" in _row_texts(win)[0])
    win.ext_remove_btn.click()
    check("開発用の削除は clone を消さない", mine.is_dir() and win.ext_table.rowCount() == 0)

    win.close()
    external_load.unload()


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
