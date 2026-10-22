"""アニメーション作業台の骨格のスモーク（AN-1。mayapy・ヘッドレス。dx11 / Render Override は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_session_smoke.py

確かめること: 殻にタブ「Animation」が載る・5 つのサブタブ・Ctrl+Z 相当（tool.undo）が session に届く・
新規 → 保存 → シーンの fileInfo → 開き直しで戻る・壊れた JSON は例外にならず警告・dx11Shader を読み込まない。
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

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # maya.standalone より先に作る

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project, shell
    from tdrive_anim import session as S
    from tdrive_anim import ui as anim_ui

    project.set_root(tmp)
    make_mini_rig.build()

    # --- 殻に載る
    ids = [t.id for t in shell.tools()]
    check("殻に Animation が登録される", "anim" in ids and ids.index("anim") > ids.index("facial"), str(ids))
    tool = shell.get_tool("anim")
    check("Animation は読み込み失敗の代わりのタブではない", isinstance(tool, anim_ui.AnimTool), type(tool).__name__)
    w = tool.build_widget()
    labels = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    check("5 つのサブタブ", labels == ["命名", "クリップ", "ブレンド", "ショット", "出力"], str(labels))

    # --- セッション: 新規 → Undo → 保存 → 開き直し
    s = S.current()
    check("最初は animset 無し", s.data is None)
    check("Undo できるものが無いと False", tool.undo() is False)
    s.new("MisotenChara", meshes=["SK_MisotenChara_Body"])
    check("新規で animset ができる", s.data and s.data["modelId"] == "MisotenChara" and s.dirty)
    check("既定の保存先は anim/<ModelId>/animset.json", s.path == tmp / "anim" / "MisotenChara" / "animset.json", str(s.path))
    s.add_clip(S.A.default_clip("A_MisotenChara_Idle", 0, 60, "MisotenChara"))
    check("クリップを足せる", len(s.data["clips"]) == 1)
    try:
        s.add_clip(S.A.default_clip("A_MisotenChara_Idle", 0, 5, "MisotenChara"))
        check("重複名はエラー", False)
    except S.A.AnimSetError:
        check("重複名はエラー", len(s.data["clips"]) == 1)
    check("失敗した操作は Undo に積まれない", len(s._undo) == 2, str(len(s._undo)))  # new + add_clip
    check("tool.undo で 1 操作戻る", tool.undo() and len(s.data["clips"]) == 0)
    check("tool.redo でやり直せる", tool.redo() and len(s.data["clips"]) == 1)
    try:
        s.new("misoten")
        check("ModelId の形が違うと拒否", False)
    except S.A.AnimSetError:
        check("ModelId の形が違うと拒否", True)

    path = s.save()
    check("保存でファイルができる", path.exists() and not s.dirty)
    check("fileInfo に場所を覚える", cmds.fileInfo(S.PATH_KEY, query=True) == ["anim/MisotenChara/animset.json"], str(cmds.fileInfo(S.PATH_KEY, query=True)))
    s.data = None  # 開き直しを模す
    S.on_scene_opened()
    check("シーンを開くと自動で読む", s.data is not None and len(s.data["clips"]) == 1)

    # --- 壊れた JSON は警告だけ
    path.write_text("{broken", encoding="utf-8")
    s.data = None
    S.on_scene_opened()  # 例外にならない
    check("壊れた JSON でシーンを開く操作は止まらない", s.data is None)
    try:
        s.open(path)
        check("open は AnimSetError を返す", False)
    except S.A.AnimSetError:
        check("open は AnimSetError を返す", True)

    # --- 画面の更新
    s.new("MisotenChara")
    APP.processEvents()
    check("ヘッダーに ModelId が出る", "MisotenChara" in w.header.label.text())
    w.detach()
    check("detach で購読が外れる", w.refresh not in s.listeners)

    # --- DirectX 11 に依存しない
    check("dx11Shader を読み込んでいない", not cmds.pluginInfo("dx11Shader", query=True, loaded=True) if cmds.pluginInfo("dx11Shader", query=True, registered=True) else True)
    src = "".join(p.read_text(encoding="utf-8") for p in (REPO / "maya/scripts/tdrive_anim").rglob("*.py"))
    check("tdrive_anim は dx11 / screen_line / parity_status を参照しない",
          not any(k in src for k in ("import screen_line", "parity_status", "dx11Shader\", quiet")), "")


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
