"""Phase AN コードレビュー（docs/21）のセッション・画面まわりの回帰スモーク（mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_review_session_smoke.py

UX-1 タブを切り替えると描き直す / UX-2 型の壊れた animset でも殻が止まらない / UX-3 新規が既存のファイルを上書きしない /
SF-9 Undo が保存先も戻す / SF-10 新しいシーンで持ち越さない / SF-14 実際の保存 → 開き直しで animset の場所が残る /
CR-3 リロードの引き継ぎ / CR-5 再生の控えを持ち越さない。
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
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_review_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    import json

    from maya import cmds

    import make_mini_rig
    from tdrive import lifecycle, project
    from tdrive_anim import clips as MC
    from tdrive_anim import session as S
    from tdrive_anim import ui as anim_ui
    from tdrive_anim.core import animset as A

    project.set_root(tmp)
    make_mini_rig.build()
    s = S.current()
    errors: list[str] = []
    lifecycle.add_error_listener(lambda summary, detail: errors.append(summary))

    # ---- UX-1: タブを切り替えると、前に出ていなかった間の変更が見える
    panel = anim_ui.AnimPanel()
    check("UX-1: animset が無いとクリップの「追加」は灰色", not panel.clips_tab.add_btn.isEnabled())
    s.new("MisotenChara", meshes=["SK_MisotenChara_Body"])
    check("UX-1: 命名タブが前面のあいだ、クリップタブは古いまま（前提）", not panel.clips_tab.add_btn.isEnabled())
    panel.tabs.setCurrentWidget(panel.clips_tab)
    APP.processEvents()
    check("UX-1: クリップタブに切り替えると描き直されて「追加」が使える", panel.clips_tab.add_btn.isEnabled())
    s.add_clip(A.default_clip("A_MisotenChara_Idle", 0, 30, "MisotenChara"))
    panel.tabs.setCurrentWidget(panel.export_tab)
    s.add_clip(A.default_clip("A_MisotenChara_Run_F", 40, 58, "MisotenChara"))  # 出力タブが前面のあいだに足す
    panel.tabs.setCurrentWidget(panel.clips_tab)
    check("UX-1: 前面でなかった間に足したクリップがクリップタブに出る", panel.clips_tab.table.rowCount() == 2, str(panel.clips_tab.table.rowCount()))

    # ---- UX-3: 新規が既存の animset を上書きしない
    default = s.default_path("MisotenChara")
    s.save()  # 最初の保存（まだファイルは無い）
    check("UX-3: 最初の保存でファイルができる", default.exists())
    before = default.read_text(encoding="utf-8")
    check("UX-3: 既定の保存先に既存があると existing_default が返す", s.existing_default("MisotenChara") == default)
    s.new("MisotenChara")
    try:
        s.save()
        check("UX-3: 既存のファイルがある新規は、確認なしの保存を拒否", False)
    except A.AnimSetExists:
        check("UX-3: 既存のファイルがある新規は、確認なしの保存を拒否", default.read_text(encoding="utf-8") == before)
    cmds.fileInfo(remove=S.PATH_KEY)
    S.on_before_save()
    cmds.file(rename=str(tmp / "scene_a.ma"))
    check("UX-3: 確かめていない既存の場所は fileInfo に記録しない", not (cmds.fileInfo(S.PATH_KEY, query=True) or [""])[0])
    s.confirm_overwrite()
    s.save()
    check("UX-3: 確認したあとは保存できる", len(json.loads(default.read_text(encoding="utf-8"))["clips"]) == 0)

    # ---- SF-9: Undo が保存先とショットの保存先も戻す
    s.open(default)  # 空の animset A を開く
    s.add_clip(A.default_clip("A_MisotenChara_Idle", 0, 30, "MisotenChara"))
    s.save()
    path_a = s.path
    s.new("OtherChara")
    check("SF-9: 新規で保存先が OtherChara になる", s.path == s.default_path("OtherChara"))
    s.undo()
    check("SF-9: Undo で中身も保存先も MisotenChara に戻る", s.model_id == "MisotenChara" and s.path == path_a, f"{s.model_id} {s.path}")
    s.redo()
    check("SF-9: Redo で OtherChara に戻る", s.model_id == "OtherChara" and s.path == s.default_path("OtherChara"))
    s.undo()

    # ---- TS-6: 状態図は保存して開き直すと戻る
    from tdrive_anim.core import state_machine as SM

    s.open(path_a)
    s.add_clip(A.default_clip("A_MisotenChara_Run_F", 40, 58, "MisotenChara"))
    graph = SM.default_graph(s.data["clips"], "MisotenChara")
    s.set_value("stateGraph", graph)
    s.save()
    s.close()
    s.open(path_a)
    check("TS-6: 状態図が保存され、開き直すと戻る", s.data.get("stateGraph") == graph and len(graph["transitions"]) == 8)

    # ---- UX-2: 型の壊れた animset を開こうとしても、殻（タブ）は作れて、理由が知らされる
    bad = json.loads(path_a.read_text(encoding="utf-8"))
    bad["clips"][0]["start"] = "a"
    bad["layers"] = ["Base"]
    path_a.write_text(json.dumps(bad), encoding="utf-8")
    s.close()
    cmds.fileInfo(S.PATH_KEY, project.to_project_path(path_a))
    errors.clear()
    S.on_scene_opened()
    check("UX-2: 型の壊れた animset は開かない（理由を知らせる）", s.data is None and any("animset" in e for e in errors), str(errors))
    try:
        anim_ui.AnimPanel()
        check("UX-2: Animation タブは作れる", True)
    except Exception:
        check("UX-2: Animation タブは作れる", False, traceback.format_exc())
    path_a.write_text(json.dumps(json.loads(A.dumps(A.new_animset("MisotenChara")))), encoding="utf-8")

    # ---- SF-14: 実際の保存 → 開き直しで animset の場所が残る（SceneSaved の後で fileInfo を書いても保存済みのファイルには入らない）
    s.close()
    cmds.file(new=True, force=True)
    make_mini_rig.build()
    s.new("ThirdChara")
    s.add_clip(A.default_clip("A_ThirdChara_Idle", 0, 30, "ThirdChara"))
    scene = tmp / "scene_c.ma"
    cmds.file(rename=str(scene))
    cmds.file(save=True, type="mayaAscii")  # kBeforeSave のコールバックが場所を fileInfo に入れる
    S.on_scene_saved()  # SceneSaved の scriptJob 相当
    check("SF-14: 保存で animset.json ができる", s.default_path("ThirdChara").exists() and not s.dirty)
    s.close()
    cmds.file(str(scene), open=True, force=True)
    S.on_scene_opened()
    check("SF-14: 保存済みのシーンを開くと animset が自動で開く", s.data is not None and s.model_id == "ThirdChara" and len(s.data["clips"]) == 1,
          str(cmds.fileInfo(S.PATH_KEY, query=True)))

    # ---- SF-10: 新しいシーン・記録の無いシーンに前のシーンの animset を持ち越さない
    errors.clear()
    s.add_clip(A.default_clip("A_ThirdChara_Run_F", 40, 58, "ThirdChara"))  # 未保存
    cmds.file(new=True, force=True)
    S.on_new_scene()
    check("SF-10: 新しいシーンでは閉じる", s.data is None and s.shot is None)
    check("SF-10: 未保存の変更を捨てたことを知らせる", any("破棄" in e for e in errors), str(errors))
    s.open(s.default_path("ThirdChara"))  # きれいな状態で開いたまま
    cmds.file(new=True, force=True)
    S.on_scene_opened()  # 記録の無いシーン（fileInfo なし）
    check("SF-10: 記録の無いシーンを開くと、保存済みの animset は閉じる", s.data is None)
    s.open(s.default_path("ThirdChara"))
    s.add_clip(A.default_clip("A_ThirdChara_Jump", 60, 78, "ThirdChara"))
    errors.clear()
    S.on_scene_opened()
    check("SF-10: 未保存があるときは開いたまま、知らせる", s.data is not None and any("未保存" in e for e in errors), str(errors))
    s.close()

    # ---- CR-3: リロードで未保存の変更・Undo を引き継ぐ
    s.new("MisotenChara")
    s.add_clip(A.default_clip("A_MisotenChara_Idle", 0, 30, "MisotenChara"))
    st = s.export_state()
    undo_len = len(s._undo)
    s.close()
    check("CR-3: 閉じると何も無い", s.export_state() is None)
    ok = s.import_state(st)
    check("CR-3: 引き継ぎで未保存の変更・Undo が戻る", ok and s.dirty and len(s.data["clips"]) == 1 and len(s._undo) == undo_len, f"{len(s._undo)} {undo_len}")
    s.close()

    # ---- CR-5: 別のシーンを開いたら再生前の控えを捨てる
    MC.play({"name": "A_X", "start": 0, "end": 10, "loop": True})
    check("CR-5: 再生中", MC.is_playing())
    cmds.file(new=True, force=True)
    S.on_new_scene()
    check("CR-5: 新しいシーンで控えを捨てる（前のシーンの範囲を書き戻さない）", not MC.is_playing())

    # ---- コールバックは重複しない
    S.install_scene_callbacks()
    S.install_scene_callbacks()
    check("保存直前のコールバックは 1 つだけ", len(getattr(sys, S._CALLBACKS_ATTR)) == 1)


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
