"""プレビュー（rig）があるときのレイヤーの追加・改名・削除・無効化のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_preview_layers_smoke.py

再現した不具合: プレビューを作ったあとで空のレイヤー「Temp」を足すと、rig に無い `emotion_Temp` を読んで
「画面の更新でエラー: 名前と一致するオブジェクトがありません」が出た。
手順（守らないと落ちる）: QT_QPA_PLATFORM=offscreen → QApplication を作る → maya.standalone.initialize。
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
ERRORS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import lifecycle, project
    from tdrive_facial import preview_rig as pr
    from tdrive_facial import scene
    from tdrive_facial import session as S
    from tdrive_facial import ui
    from tdrive_facial.core import autofill, fcpose_io

    lifecycle.report_error = lambda summary, detail="", once=True: ERRORS.append(f"{summary}\n{detail}")  # 通知を集める

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fprev_layers_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    S.FacialSession.model_cameras = staticmethod(lambda: ["persp"])
    doc0 = facial_fixture.make_doc()
    autofill.generate_from_keys(doc0)
    p0 = tmp / "mini.fcpose.json"
    fcpose_io.save(doc0, p0)

    panel = ui.FacialPanel()
    panel.resize(560, 900)
    s.open(p0)
    s.bake_all()
    panel.select_tab("grid")
    pv = panel.tab("grid").preview
    pv.btn_build.click()
    pump()
    rig = s.preview_rig_node()
    check("準備: rig ができて動作中（emotion_Joy だけ）", s.preview_state() == "live" and cmds.attributeQuery("emotion_Joy", node=rig, exists=True), s.preview_state())

    def emo_attrs() -> set[str]:
        return {a for a in (cmds.listAttr(rig, userDefined=True) or []) if a.startswith("emotion_")}

    def names() -> list[str]:
        return [layer.name for layer in s.doc.layers]

    def idx(name: str) -> int:
        return names().index(name)

    def no_error(tag: str) -> None:
        pump()
        st = s.preview_status()  # 例外にならない
        ro = s.preview_readout()
        check(f"{tag}: エラー通知なし・状態を読める", not ERRORS and st is not None and ro is not None, "\n".join(ERRORS))

    def match(tag: str) -> None:
        want = set(pr.emotion_attrs(s.doc).values())
        check(f"{tag}: rig の感情アトリビュート = レイヤー一覧", emo_attrs() == want, f"{sorted(emo_attrs())} vs {sorted(want)}")

    # ------------------------------------------------------------ 追加
    s.add_layer("Temp")
    no_error("追加")
    check("追加: 自動で作り直されて動作中（古い印なし）", s.preview_state() == "live" and not s.preview_is_stale(), s.preview_state())
    match("追加")
    check("追加: emotion_Temp の値は 0・スライダーの行があって押せる", abs(cmds.getAttr(rig + ".emotion_Temp")) < 1e-9 and "emotion_Temp" in pv._emotion_rows and pv._emotion_rows["emotion_Temp"][1].isEnabled())

    # ------------------------------------------------------------ 改名（キーが付いていく）
    cmds.setKeyframe(rig + ".emotion_Temp", time=1, value=0.2)
    cmds.setKeyframe(rig + ".emotion_Temp", time=10, value=0.8)
    s.rename_layer(idx("Temp"), "Temp2")
    no_error("改名")
    match("改名")
    keys = cmds.keyframe(rig + ".emotion_Temp2", query=True, valueChange=True) or []
    check("改名: キーが新しい名前のアトリビュートに付いていく（値 0.2 / 0.8）", [round(k, 3) for k in keys] == [0.2, 0.8] and "emotion_Temp" not in emo_attrs(), str(keys))
    check("改名: 動作中・行が入れ替わる", s.preview_state() == "live" and "emotion_Temp2" in pv._emotion_rows and "emotion_Temp" not in pv._emotion_rows)

    # ------------------------------------------------------------ 削除（キーも消える）
    curves_before = set(cmds.ls(type="animCurve") or [])
    s.delete_layer(idx("Temp2"))
    no_error("削除")
    match("削除")
    check("削除: 宙ぶらりんのアトリビュートもキーのカーブも残らない", "emotion_Temp2" not in emo_attrs() and len(cmds.ls(type="animCurve") or []) < len(curves_before), str(sorted(emo_attrs())))
    check("削除: 動作中", s.preview_state() == "live")

    # ------------------------------------------------------------ 無効化・有効化（ベイク済みのレイヤー）
    s.set_layer_enabled(idx("Joy"), False)
    no_error("無効化")
    match("無効化")
    check("無効化: 動作中・Joy のスライダー行は残る", s.preview_state() == "live" and "emotion_Joy" in pv._emotion_rows)
    s.set_layer_enabled(idx("Joy"), True)
    no_error("有効化")
    check("有効化: 動作中", s.preview_state() == "live" and not s.preview_is_stale())

    # ------------------------------------------------------------ 編集中: 保留 → 行は押せない表示 → 編集を終えると作り直し
    s.begin_edit()
    s.add_layer("Tmp3")
    no_error("編集中の追加")
    check("編集中の追加: rig はまだ古い（保留）・古い印", s.preview_state() == "stale" and "emotion_Tmp3" not in emo_attrs() and not pv.stale_note.isHidden())
    row = pv._emotion_rows.get("emotion_Tmp3")
    check("編集中の追加: 行はあるが押せず「プレビューを作り直すと使えます」", row is not None and not row[1].isEnabled() and "プレビューを作り直すと使えます" in row[2].text(), "" if row is None else row[2].text())
    try:
        s.preview_set_attr("emotion_Tmp3", 0.5)
        check("編集中の追加: 未作成の値を設定すると分かる説明で断る", False, "例外にならなかった")
    except S.FacialSessionError as exc:
        check("編集中の追加: 未作成の値を設定すると分かる説明で断る", "作り直す" in str(exc), str(exc))
    s.end_edit()
    no_error("編集を終える")
    match("編集を終える")
    check("編集を終える: 自動で作り直され、行が押せる", s.preview_state() == "live" and pv._emotion_rows["emotion_Tmp3"][1].isEnabled())

    # ------------------------------------------------------------ キーに焼いた状態（式なし）: 作り直しはしない・行は押せない表示
    s.preview_bake_to_keys(1, 2)
    check("キーに焼く: 状態 keys", s.preview_state() == "keys")
    s.add_layer("Tmp4")
    no_error("キーに焼いた状態の追加")
    row = pv._emotion_rows.get("emotion_Tmp4")
    check("キーに焼いた状態の追加: 行は押せず「プレビューを作り直すと使えます」", row is not None and not row[1].isEnabled() and "プレビューを作り直すと使えます" in row[2].text(), "" if row is None else row[2].text())
    s.preview_clear_keys()  # 焼いたキーが残ると配線されない（ボタンだと確認ダイアログが出て画面なしでは止まる）
    s.preview_build()
    pump()
    no_error("作り直す")
    match("作り直す")
    check("作り直す: 動作中・行が押せる", s.preview_state() == "live" and pv._emotion_rows["emotion_Tmp4"][1].isEnabled())

    # ------------------------------------------------------------ 旧い rig（レイヤー名の記録なし）でも落ちない
    cmds.deleteAttr(rig + ".tdFacialLayers")
    s.rename_layer(idx("Tmp4"), "Tmp5")
    no_error("記録のない rig の改名")
    match("記録のない rig の改名")

    pv.btn_delete.click()
    pump()
    panel.detach()
    s.close()
    shutil.rmtree(tmp, ignore_errors=True)


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
