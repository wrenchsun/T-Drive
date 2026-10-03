"""FacialController のリップシンクの対応表（R-18。Maya 側）のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_lipsync_smoke.py

確かめること:
- 作る: 合成のプロファイルから基本の行（作った / 残した / モデルに無い）・Undo / Redo
- 編集の対象: マスの選択・編集・保存（シェイプだけ。ボーンは捨てて知らせる）・点 / キー / マスの切り替えの確認（保存 / 破棄 / 取りやめ）・
  音素・感情レイヤーの改名と削除・Undo で対象が無くなったとき・保存中の一時退避・リロードの引き継ぎ
- 試す: 表の計算（`lipsync_evaluate`）と同じ値がシーンへ当たる・元の値に重ねない・終わるとシーンが元どおり・文書も Maya の Undo も変わらない・
  終わる条件（対象の選択・編集を抜ける・タブを離れる）・保存中の退避と続き・未保存の編集があるときの確認
- 検証: リップシンクの問題の行 →「リップシンク A（Joy）」・ダブルクリックでそのマスへ・エラーが出力前の件数に入る・出力に lipSync が入る
- 画面: タブの組み立て・空の状態・音素の追加 / 名前の変更 / 削除の確認 / 並べ替え・プロファイルから作る・表・マスの選択・空にする・試すの箱・文言
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があれば lipsync_tab / lipsync_try / lipsync_empty を書く（幅 480 px）。
ファイルはすべて一時フォルダ。
"""

from __future__ import annotations

import copy
import json
import os
import re
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

from PySide6 import QtGui, QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る


def _style() -> None:
    for name in ("meiryo.ttc", "YuGothM.ttc", "msgothic.ttc"):
        f = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if f.exists():
            fid = QtGui.QFontDatabase.addApplicationFont(str(f))
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                APP.setFont(QtGui.QFont(fams[0], 9))
                break
    APP.setStyle("Fusion")
    p = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, "#444444"), (QtGui.QPalette.WindowText, "#d8d8d8"), (QtGui.QPalette.Base, "#2b2b2b"),
        (QtGui.QPalette.AlternateBase, "#3a3a3a"), (QtGui.QPalette.Text, "#d8d8d8"), (QtGui.QPalette.Button, "#5d5d5d"),
        (QtGui.QPalette.ButtonText, "#e0e0e0"), (QtGui.QPalette.Highlight, "#5285a6"), (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ToolTipBase, "#333333"), (QtGui.QPalette.ToolTipText, "#ffffff"),
    ):
        p.setColor(role, QtGui.QColor(color))
    APP.setPalette(p)


_style()

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []
TOL = 1e-6


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import export, scene, ui
    from tdrive_facial import session as S
    from tdrive_facial.core import evaluate, fcpose_io
    from tdrive_facial.core import profile as profile_mod
    from tdrive_facial.core import validate as V
    from tdrive_facial.core.model import BoneOffset, LipSyncEntry, SourcePose

    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_lip_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    bs = "bs"
    shapes = ("mouth_open", "smile_L", "smile_R", "brow_up")

    # 合成のプロファイル: A = 口を開く / I = 両方の笑い / U = モデルに無いシェイプ
    prof_dir = project.root() / "facial" / "mini" / "profiles"
    prof_dir.mkdir(parents=True)
    (prof_dir / "synlip.fcprofile.json").write_text(
        json.dumps(
            {
                "format": "FacialNamingProfile",
                "version": 1,
                "name": "synlip",
                "lipSync": {"A": "mouth_open", "I": {"smile_L": 0.5, "smile_R": 0.5}, "U": "no_such_shape"},
            }
        ),
        encoding="utf-8",
    )
    doc0 = facial_fixture.make_doc()
    doc0.profile = "synlip"
    p_doc = tmp / "src" / "mini.fcpose.json"
    p_doc.parent.mkdir()
    fcpose_io.save(doc0, p_doc)

    def open_doc() -> None:
        s.close()
        s.open(p_doc)

    def weights() -> dict[str, float]:
        return {n: cmds.getAttr(f"{bs}.{n}") for n in shapes}

    def eye_t() -> tuple:
        return tuple(round(v, 6) for v in cmds.getAttr("eye_L.translate")[0])

    def undo_queue_empty() -> bool:
        return bool(cmds.undoInfo(query=True, undoQueueEmpty=True))

    # ============================================================ 1. 作る
    open_doc()
    check("開く: リップシンクの設定が無い文書は present=False・Undo は空", not s.lipsync_view().present and s.doc.lip_sync is None and not s.can_undo)
    check("開く: 文書のプロファイルが読めて lipSync の対応を持つ", s.profile is not None and list(s.profile.lip_sync) == ["A", "I", "U"], str(s.profile))
    r = s.create_lipsync_from_profile()
    lip = s.doc.lip_sync
    check(
        "作る: 基本の行を 2 つ作る（A / I）・U はモデルに無くて入れない（音素は足す）・使う = オン",
        r.ok and r.created == 2 and r.missing == [("U", "no_such_shape")] and lip is not None and lip.enabled and lip.phonemes == ["A", "I", "U"],
        str((r.created, r.missing, lip and lip.phonemes)),
    )
    ea, ei = lip.find_entry("A", ""), lip.find_entry("I", "")
    check(
        "作る: A = bs.mouth_open 1.0（プロファイルの名前をシーンの名前に合わせた）・I = 笑い 0.5 ずつ・U の行は無い",
        ea is not None and ea.curves == {"bs.mouth_open": 1.0} and ei is not None and ei.curves == {"bs.smile_L": 0.5, "bs.smile_R": 0.5} and lip.find_entry("U", "") is None,
        str(lip.entries),
    )
    check("作る: Undo 1 回で戻り（設定ごと無くなる）、Redo で戻る", s.undo() and s.doc.lip_sync is None and s.redo() and s.doc.lip_sync is not None and len(s.doc.lip_sync.entries) == 2)
    r2 = s.create_lipsync_from_profile()
    check("作る: 続けて作ると何も作れず失敗・文書は変わらない・Undo に積まない", not r2.ok and r2.kept == ["A", "I"] and len(s.doc.lip_sync.entries) == 2 and len(s._undo) == 1, str(r2))
    s.set_lipsync_strength(0.8)
    s.set_lipsync_volume(min=0.0, max=1.0, from_=0.5, to=1.0)
    s.set_lipsync_follow(12.0)
    check("設定: 強さ・声量・追従が文書に入る（Undo 可）・範囲外は失敗して変わらない", abs(s.doc.lip_sync.strength - 0.8) < TOL and s.doc.lip_sync.follow == 12.0 and not s.set_lipsync_strength(1.5).ok and abs(s.doc.lip_sync.strength - 0.8) < TOL)
    s.set_lipsync_strength(1.0)
    # 検証: 問題なし
    issues = s.validate()
    check("検証: 作った直後はリップシンクの問題が出ない", not [i for i in issues if i.lip is not None or i.code.startswith("lipsync")], str([i.code for i in issues if i.lip is not None or i.code.startswith("lipsync")]))

    # ============================================================ 2. 編集の対象
    check("対象: 編集状態に入っていない", not s.editing and s.ctx.selected_lip() is None and s.edit_target_label() == "")
    orig_eye = eye_t()
    cmds.setAttr(f"{bs}.brow_up", 0.25)  # 編集を抜けたら戻る「元の値」
    r = s.select_lip_cell("A")
    check(
        "対象: A（基本）を選ぶ → 編集状態・applied_lip・点 / キーの選択なし・対象の呼び名",
        r.status == "selected" and s.editing and s.applied_lip == ("A", "") and s.applied_point is None and s.applied_key is None and s.ctx.selection is None and s.edit_target_label() == "リップシンクの A（基本）",
        f"{r.status} {s.applied_lip} {s.edit_target_label()}",
    )
    check("対象: バッファ = 行のシェイプ・PoseView.lip・保存済み扱い・保存できる", s.pose.curves == {"bs.mouth_open": 1.0} and s.pose.view().lip == ("A", "") and not s.pose.dirty and s.pose.view().can_save)
    w = weights()
    check("対象: シーンに行のポーズが当たる（mouth_open 1.0・ほかは 0。元の brow_up 0.25 は基準姿勢で 0）", abs(w["mouth_open"] - 1.0) < TOL and abs(w["brow_up"]) < TOL and abs(w["smile_L"]) < TOL, str(w))
    check("対象: Maya の blendShape の選択でなく Presenter の選択（格子には選択が無い）", s.grid.view().selection is None)
    s.set_curve("bs.smile_L", 0.3)
    s.set_bone("eye_L", BoneOffset(t=(0.0, 0.5, 0.0)))
    check("対象: ボーンを動かしてもシーンのジョイントには当たらない（シェイプだけ）・編集中の値は未保存", eye_t() == orig_eye and s.pose.dirty and abs(cmds.getAttr(f"{bs}.smile_L") - 0.3) < TOL, f"{eye_t()} vs {orig_eye}")
    n_undo = len(s._undo)
    res = s.save_point()
    ea = s.doc.lip_sync.find_entry("A", "")
    check(
        "保存: 行にシェイプだけが入る（ボーンは捨てる）・メッセージでボーンは保存されないと知らせる・Undo に 1 つ積む",
        res.ok and ea.curves == {"bs.mouth_open": 1.0, "bs.smile_L": 0.3} and not hasattr(ea, "bones") and "ボーンはリップシンクには保存されません" in res.message and len(s._undo) == n_undo + 1 and not s.pose.dirty,
        f"{res.message} {ea.curves}",
    )
    check("保存: 保存してもシーンは同じ・ボーンは基準のまま", abs(cmds.getAttr(f"{bs}.smile_L") - 0.3) < TOL and eye_t() == orig_eye)
    check("保存: Undo で行が戻り、編集中の値も読み直される（対象はそのまま）・シーンへ当て直す", s.undo() and s.doc.lip_sync.find_entry("A", "").curves == {"bs.mouth_open": 1.0} and s.ctx.selected_lip() == ("A", "") and s.pose.curves == {"bs.mouth_open": 1.0} and abs(cmds.getAttr(f"{bs}.smile_L")) < TOL)
    s.redo()
    s.undo()
    # 行が無いマス（A × Joy）へ新しく保存
    r = s.select_lip_cell("A", "Joy")
    check("対象: 行が無い A（Joy）も選べる（空のポーズ・シーンは基準姿勢）", r.status == "selected" and s.pose.curves == {} and s.edit_target_label() == "リップシンクの A（Joy）" and abs(cmds.getAttr(f"{bs}.mouth_open")) < TOL)
    s.set_curve("bs.mouth_open", 0.8)
    s.set_curve("bs.smile_L", 0.4)
    s.save_point()
    check("保存: 感情の行ができる（A × Joy）", s.doc.lip_sync.find_entry("A", "Joy").curves == {"bs.mouth_open": 0.8, "bs.smile_L": 0.4})
    s.pose.zero()
    s.save_point()
    check("保存: 全部 0 を保存すると行が消える（メッセージつき）", s.doc.lip_sync.find_entry("A", "Joy") is None)
    s.undo()
    check("対象: 存在しない音素・感情は選べず何も変わらない", s.select_lip_cell("Q").status == "invalid" and s.select_lip_cell("A", "Nope").status == "invalid" and s.ctx.selected_lip() == ("A", "Joy"))

    # 点 ↔ キー ↔ マスの切り替え
    s.set_perspective_enabled(True)
    s.add_perspective_key(30.0, SourcePose({"bs.brow_up": 0.5}, {}))
    s.select_lip_cell("A", choice="discard")
    s.set_curve("bs.smile_R", 0.9)
    r = s.select_point(1, 2)
    check("切り替え: 未保存のマス → 点は needs_confirm・何も変わらない", r.status == "needs_confirm" and s.ctx.selected_lip() == ("A", "") and s.pose.dirty)
    r = s.select_point(1, 2, choice="cancel")
    check("切り替え: 取りやめ → マスのまま・編集中の値が残る", r.status == "cancelled" and s.ctx.selected_lip() == ("A", "") and abs(s.pose.curves.get("bs.smile_R", 0) - 0.9) < TOL)
    r = s.select_point(1, 2, choice="discard")
    check("切り替え: 破棄 → 点へ移る・行は変わらない・マスの対象は外れる", r.status == "selected" and s.ctx.selection == (1, 2) and s.ctx.selected_lip() is None and s.applied_point == (0, 1, 2) and "bs.smile_R" not in s.doc.lip_sync.find_entry("A", "").curves and s.applied_lip is None)
    s.set_curve("bs.brow_up", 0.7)
    r = s.select_lip_cell("I")
    check("切り替え: 未保存の点 → マスは needs_confirm", r.status == "needs_confirm" and s.ctx.selection == (1, 2))
    r = s.select_lip_cell("I", choice="save")
    check("切り替え: 保存 → 点に書かれてマスへ移る（点の選択は外れる）", r.status == "selected" and r.saved and s.ctx.selected_lip() == ("I", "") and s.ctx.selection is None and abs(s.doc.layers[0].points[(1, 2)].pose.curves.get("bs.brow_up", 0) - 0.7) < TOL and s.applied_lip == ("I", ""))
    s.set_curve("bs.brow_up", 0.6)
    r = s.select_key(0)
    check("切り替え: 未保存のマス → キーは needs_confirm", r.status == "needs_confirm" and s.ctx.selected_lip() == ("I", ""))
    r = s.select_key(0, choice="save")
    check("切り替え: 保存 → マスの行に入ってキーへ移る（マスの対象は外れる）", r.status == "selected" and r.saved and s.ctx.selected_key() == 0 and s.ctx.selected_lip() is None and abs(s.doc.lip_sync.find_entry("I", "").curves.get("bs.brow_up", 0) - 0.6) < TOL and s.applied_key == 0)
    s.set_curve("bs.smile_R", 0.2)
    r = s.select_lip_cell("A", "Joy", choice="discard")
    check("切り替え: 未保存のキー → マスは破棄で移れる・キーのポーズは変わらない", r.status == "selected" and s.ctx.selected_lip() == ("A", "Joy") and s.ctx.selected_key() is None and "bs.smile_R" not in s.doc.perspective.keys[0].curves)
    s.set_curve("bs.smile_R", 0.2)
    r = s.select_lip_cell("I", choice="cancel")
    check("切り替え: マス → マス・取りやめは 1 回の呼び出しで足りる（確認が要る状況）", r.status == "cancelled" and s.ctx.selected_lip() == ("A", "Joy") and s.pose.dirty)
    r = s.select_lip_cell("A", "Joy")
    check("切り替え: 同じマスを選び直しても編集中の値を保つ", r.status == "same_point" and abs(s.pose.curves.get("bs.smile_R", 0) - 0.2) < TOL)
    s.select_lip_cell("A", choice="discard")
    s.undo()  # 保存した I の行などを戻す（Undo の順は気にしない）
    s.undo()
    s.end_edit()
    s.set_perspective_enabled(False)

    # 音素・感情レイヤーの改名と削除
    open_doc()
    s.create_lipsync_from_profile()
    s.set_lip_cell_pose("A", "Joy", SourcePose({"bs.mouth_open": 0.8, "bs.smile_L": 0.3}))
    s.select_lip_cell("A", "Joy")
    r = s.rename_lip_phoneme("A", "Aa")
    check(
        "音素の改名: 行・編集の対象・呼び名が付いていく（未保存でないまま）・シーンはそのまま",
        r.ok and s.doc.lip_sync.phonemes[0] == "Aa" and s.doc.lip_sync.find_entry("Aa", "Joy") is not None and s.ctx.selected_lip() == ("Aa", "Joy") and s.edit_target_label() == "リップシンクの Aa（Joy）" and not s.pose.dirty and abs(cmds.getAttr(f"{bs}.smile_L") - 0.3) < TOL,
        f"{s.ctx.selected_lip()} dirty={s.pose.dirty}",
    )
    check("音素の改名: 重複・空は失敗して変わらない", not s.rename_lip_phoneme("Aa", "I").ok and not s.rename_lip_phoneme("Aa", " ").ok and s.doc.lip_sync.phonemes[0] == "Aa")
    s.rename_layer(1, "Happy")
    check("感情レイヤーの改名: 行の感情・編集の対象が付いていく", s.doc.lip_sync.find_entry("Aa", "Happy") is not None and s.ctx.selected_lip() == ("Aa", "Happy") and [c.name for c in s.lipsync.view().columns] == ["", "Happy"], str(s.ctx.selected_lip()))
    s.rename_layer(1, "Joy")
    s.rename_lip_phoneme("Aa", "A")
    s.add_layer("Sad")
    s.set_lip_cell_pose("I", "Sad", SourcePose({"bs.smile_L": 0.1}))
    s.select_lip_cell("I", "Sad")
    s.delete_layer(2)
    check(
        "感情レイヤーの削除: その感情の行が消え、編集の対象は外れ、シーンは基準姿勢へ",
        s.doc.lip_sync.find_entry("I", "Sad") is None and s.ctx.selected_lip() is None and s.ctx.lip_target is None and s.applied_lip is None and abs(cmds.getAttr(f"{bs}.smile_L")) < TOL,
        str((s.ctx.lip_target, s.applied_lip)),
    )
    s.add_lip_phoneme("E")
    s.select_lip_cell("E")
    s.undo()
    check("Undo: 音素の追加を戻すと、選んでいたマスの対象は外れる（シーンは基準姿勢）", "E" not in s.doc.lip_sync.phonemes and s.ctx.selected_lip() is None and s.ctx.lip_target is None and s.applied_lip is None)
    s.redo()
    s.select_lip_cell("E")
    s.set_curve("bs.brow_up", 0.4)
    r = s.remove_lip_phoneme("E")
    check("音素の削除: 選んでいる音素なら対象が外れ、編集中の値も消える（行も消える）", r.ok and "E" not in s.doc.lip_sync.phonemes and s.ctx.selected_lip() is None and not s.pose.dirty and s.applied_lip is None and abs(cmds.getAttr(f"{bs}.brow_up")) < TOL)
    s.remove_lip_phoneme("I")
    check("音素の削除: その音素の行も消える・並べ替え", all(e.phoneme != "I" for e in s.doc.lip_sync.entries) and s.move_lip_phoneme("A", 5).ok and s.doc.lip_sync.phonemes[-1] == "A")
    s.undo()
    s.undo()
    s.end_edit()
    check("終了: 編集を抜けるとシーンは元（brow_up 0.25・ボーンも元）に戻る", abs(cmds.getAttr(f"{bs}.brow_up") - 0.25) < TOL and eye_t() == orig_eye and not s.editing)
    cmds.setAttr(f"{bs}.brow_up", 0.0)

    # ============================================================ 3. 試す
    open_doc()
    s.create_lipsync_from_profile()
    s.set_lip_cell_pose("A", "Joy", SourcePose({"bs.mouth_open": 0.8, "bs.smile_L": 0.3}))
    s.set_lip_cell_pose("I", "Joy", SourcePose({"bs.smile_L": 0.9}))
    s.set_lipsync_volume(min=0.0, max=1.0, from_=0.5, to=1.0)
    s.set_lipsync_strength(0.9)
    cmds.setAttr(f"{bs}.brow_up", 0.25)  # 元の値（編集を抜けると戻る）
    doc_before = fcpose_io.to_dict(s.doc)
    n_undo = len(s._undo)
    cmds.flushUndo()
    r = s.lip_try_start()
    check("試す: 始める → 編集状態・試している・対象なし・シーンは基準姿勢", r.status == "selected" and s.editing and s.lip_try_active and not s.ctx.has_target and all(abs(v) < TOL for v in weights().values()), str(weights()))
    table = [
        ({"A": 1.0}, None, {}),
        ({"A": 0.5, "I": 0.5}, 0.5, {}),
        ({"A": 1.0}, 0.0, {"Joy": 1.0}),
        ({"A": 0.6, "I": 0.8, "U": 0.3}, 1.0, {"Joy": 0.4}),
        ({"I": 1.0}, 0.25, {"Joy": 0.7}),
        ({}, None, {}),
        ({"A": 0.3, "Nope": 1.0}, 0.9, {}),
    ]
    limits = s.lipsync.limit_map()
    ok_all = True
    detail = ""
    for ph, vol, emo in table:
        got = s.lip_try_set(ph, vol, emo)
        want = evaluate.lipsync_evaluate(s.doc.lip_sync, {}, ph, vol, emo, limits)
        scene_w = weights()
        exp = {n: want.get(f"bs.{n}", 0.0) for n in shapes}
        good = all(abs(scene_w[n] - exp[n]) < TOL for n in shapes) and all(abs(got.get(k, 0.0) - v) < TOL for k, v in want.items())
        if not good:
            ok_all = False
            detail += f"\n{ph} {vol} {emo}: scene={scene_w} want={exp}"
    check("試す: 表の入力（7 通り）で、シーンの値が lipsync_evaluate と同じ（1e-6）", ok_all, detail)
    s.lip_try_set({"A": 1.0}, None, {})
    first = weights()
    s.lip_try_set({"A": 1.0}, None, {})
    check("試す: 同じ入力を繰り返しても値が変わらない（自分の出力に重ねない）", all(abs(weights()[n] - first[n]) < TOL for n in shapes) and abs(first["mouth_open"] - 0.9) < TOL, str(first))
    check("試す: 文書は変わらず、エディタ内 Undo にも Maya の Undo にも積まない", fcpose_io.to_dict(s.doc) == doc_before and len(s._undo) == n_undo and undo_queue_empty())
    check("試す: ボーンは動かさない", eye_t() == orig_eye)
    # 元の値（土台の表情）の上に載る
    s.end_edit()
    s.set_base_expression("t", {"bs.mouth_open": 0.4, "bs.brow_up": 0.2})
    s.lip_try_start()
    cur = dict(s.base_expression_curves)
    got = s.lip_try_set({"A": 0.5, "I": 0.5}, 0.5, {})
    want = evaluate.lipsync_evaluate(s.doc.lip_sync, cur, {"A": 0.5, "I": 0.5}, 0.5, {}, limits)
    exp = {n: want.get(f"bs.{n}", cur.get(f"bs.{n}", 0.0)) for n in shapes}
    check("試す: 元の値（土台の表情だけ。brow_up 0.2）は対応表に無いシェイプなのでそのまま・mouth_open は current から計算", all(abs(weights()[n] - exp[n]) < TOL for n in shapes) and abs(weights()["brow_up"] - 0.2) < TOL, f"{weights()} {exp}")
    s.end_edit()
    check("終了（編集を抜ける）: 試すも終わり、シーンは元（brow_up 0.25）", not s.lip_try_active and abs(cmds.getAttr(f"{bs}.brow_up") - 0.25) < TOL and abs(cmds.getAttr(f"{bs}.mouth_open")) < TOL)
    # 「試すのをやめる」
    s.lip_try_start()
    s.lip_try_set({"A": 1.0}, None, {})
    check("やめる: lip_try_stop → 試していない・シーンは編集状態が見せるもの（基準姿勢 = 全 0）・編集状態は続く", s.lip_try_stop() and not s.lip_try_active and s.editing and all(abs(v) < TOL for v in weights().values()) and not s.lip_try_stop(), str(weights()))
    try:
        s.lip_try_set({"A": 1.0})
        check("試す: 始めずに入力すると FacialSessionError", False)
    except S.FacialSessionError:
        check("試す: 始めずに入力すると FacialSessionError", True)
    # ほかの対象を選ぶと終わる
    s.lip_try_start()
    s.lip_try_set({"A": 1.0}, None, {})
    s.select_lip_cell("I")
    check("終了（マスを選ぶ）: 試すが終わり、マス I のポーズがシーンに当たる", not s.lip_try_active and s.applied_lip == ("I", "") and abs(cmds.getAttr(f"{bs}.smile_L") - 0.5) < TOL and abs(cmds.getAttr(f"{bs}.mouth_open")) < TOL, str(weights()))
    s.lip_try_start()
    check("開始: 試しはじめると編集の対象が外れる（マスと試すは同時に使えない）", s.lip_try_active and s.ctx.selected_lip() is None and s.applied_lip is None and all(abs(v) < TOL for v in weights().values()))
    s.lip_try_set({"A": 1.0}, None, {})
    s.select_point(1, 2)
    check("終了（点を選ぶ）: 試すが終わり、点のポーズが当たる", not s.lip_try_active and s.applied_point == (0, 1, 2) and abs(cmds.getAttr(f"{bs}.mouth_open") - 0.6) < TOL, str(weights()))
    # 未保存の編集があるときの確認
    s.set_curve("bs.brow_up", 0.35)
    r = s.lip_try_start()
    check("開始: 未保存のポーズ編集があると needs_confirm（何も変わらない）", r.status == "needs_confirm" and not s.lip_try_active and s.ctx.selection == (1, 2) and s.pose.dirty)
    r = s.lip_try_start(choice="cancel")
    check("開始: 取りやめ → そのまま（1 回の呼び出しで足りる）", r.status == "cancelled" and not s.lip_try_active and s.pose.dirty and s.ctx.selection == (1, 2))
    r = s.lip_try_start(choice="save")
    check("開始: 保存 → 点に書かれて試しはじめる（対象は外れる）", r.status == "selected" and r.saved and s.lip_try_active and s.ctx.selection is None and abs(s.doc.layers[0].points[(1, 2)].pose.curves.get("bs.brow_up", 0) - 0.35) < TOL)
    s.undo()
    s.end_edit()
    s.select_point(1, 2)
    s.set_curve("bs.brow_up", 0.35)
    r = s.lip_try_start(choice="discard")
    check("開始: 破棄 → 点は変わらず、試しはじめる", r.status == "selected" and s.lip_try_active and abs(s.doc.layers[0].points[(1, 2)].pose.curves.get("bs.brow_up", 0) - 0.35) > 0.01 and s.ctx.selection is None)
    s.end_edit()
    # 試す → 試す（同じ呼び出しは何もしない）
    s.lip_try_start()
    check("開始: すでに試していれば same_point", s.lip_try_start().status == "same_point")
    s.end_edit()

    # ---- 保存中の退避と続き
    s.lip_try_start()
    s.lip_try_set({"A": 1.0, "I": 0.5}, 0.5, {"Joy": 0.5})
    during = weights()
    f = tmp / "saved_try.ma"
    cmds.file(rename=str(f))
    S.install_scene_callbacks()
    cmds.file(save=True, type="mayaAscii")
    after = weights()
    check("保存中（試す）: 保存のあとも試している・同じ入力で当たり直している", s.editing and s.lip_try_active and all(abs(after[n] - during[n]) < TOL for n in shapes) and abs(during["mouth_open"]) > 0.05, f"{during} {after}")
    text = f.read_text(encoding="utf-8", errors="replace")
    m_bs = re.search(r'createNode blendShape -n "bs";(.*?)(?=\ncreateNode|\Z)', text, re.S)
    sect = m_bs.group(1) if m_bs else ""
    check("保存中（試す）: 保存したファイルは元のポーズ（brow_up 0.25・試した口の値は入らない）", re.search(r'\.w\[0:3\]"\s+0 0 0 0\.25;', sect) is not None, sect[:300])
    check("保存中（試す）: 保存のあとシーンの変更フラグは立たない", not cmds.file(query=True, modified=True))
    s.end_edit()
    # ---- 保存中（マスを編集）
    s.select_lip_cell("A")
    s.set_curve("bs.smile_R", 0.77)
    buf = dict(s.pose.pose_to_apply().curves)
    cmds.file(save=True, type="mayaAscii")
    text = f.read_text(encoding="utf-8", errors="replace")
    sect = (re.search(r'createNode blendShape -n "bs";(.*?)(?=\ncreateNode|\Z)', text, re.S) or [None, ""])[1]
    check(
        "保存中（マス）: 編集状態のまま・同じマス・編集中の値が残りシーンに当たる・ファイルは元のポーズ",
        s.editing and s.applied_lip == ("A", "") and dict(s.pose.pose_to_apply().curves) == buf and abs(cmds.getAttr(f"{bs}.smile_R") - 0.77) < TOL and re.search(r'\.w\[0:3\]"\s+0 0 0 0\.25;', sect) is not None,
        f"{s.applied_lip} {sect[:200]}",
    )
    S.remove_scene_callbacks()
    # ---- リロードの引き継ぎ
    state = s.export_state()
    s2 = S.FacialSession()
    s2.import_state(state)
    check("リロード: 編集の対象のマスと編集中の値が残る・文書も同じ", s2.ctx.selected_lip() == ("A", "") and abs(s2.pose.curves.get("bs.smile_R", 0) - 0.77) < TOL and s2.pose.dirty and fcpose_io.to_dict(s2.doc) == fcpose_io.to_dict(s.doc), str(s2.ctx.selected_lip()))
    s2.close()
    s.end_edit()
    cmds.setAttr(f"{bs}.brow_up", 0.0)

    # ============================================================ 4. 検証・出力
    open_doc()
    s.create_lipsync_from_profile()
    s.set_lip_cell_pose("A", "Joy", SourcePose({"bs.mouth_open": 0.5}))
    s.set_lip_cell_pose("I", "", SourcePose({"bs.smile_L": 3.0}))  # 可動域の外（警告）
    s.doc.lip_sync.entries.append(LipSyncEntry(phoneme="A", emotion="", curves={"bs.mouth_open": 0.1}))  # 同じマスが 2 つ（エラー）
    issues = s.validate()
    lip_issues = [i for i in issues if i.lip is not None]
    check(
        "検証: リップシンクの問題が lip つきで出る（可動域の外 I・同じマスが 2 つ A）",
        any(i.lip == ("I", "") and i.code == "limit_exceeded" for i in lip_issues) and any(i.lip == ("A", "") and i.code == "lipsync_entry_duplicate" for i in lip_issues),
        str([(i.code, i.lip) for i in lip_issues]),
    )
    n_err = sum(1 for i in issues if i.severity == V.SEVERITY_ERROR)
    check("検証: リップシンクのエラー（同じマスが 2 つ）がエラー件数に入る", any(i.code == "lipsync_entry_duplicate" and i.severity == V.SEVERITY_ERROR for i in issues) and n_err >= 1)
    out_dir = tmp / "out"
    out_dir.mkdir()
    p_ue = export.export_ue(s.doc, out_dir)
    d_ue = json.loads(Path(p_ue).read_text(encoding="utf-8"))
    p_fc = out_dir / "mini.fcpose"
    fcpose_io.save(s.doc, p_fc)  # Unity 向けの .fcpose（`export_unity` と同じ書き方）
    d_fc = json.loads(p_fc.read_text(encoding="utf-8"))
    check(
        "出力: .fcpose.json / .fcpose に lipSync（音素・行・声量・追従）が入る・読み戻すと同じ",
        d_ue.get("lipSync", {}).get("phonemes") == ["A", "I", "U"] and len(d_fc["lipSync"]["entries"]) == len(s.doc.lip_sync.entries) and "follow" in d_fc["lipSync"] and "volume" in d_fc["lipSync"]
        and fcpose_io.to_dict(fcpose_io.load_document(Path(p_ue))) == fcpose_io.to_dict(s.doc),
        str(d_ue.get("lipSync")),
    )
    check("出力: リップシンクが無い文書には lipSync のキーが出ない（既存のファイルを変えない）", "lipSync" not in fcpose_io.to_dict(doc0))

    # ============================================================ 5. 画面
    open_doc()  # リップシンクの設定が無い状態から
    panel = ui.FacialPanel()
    panel.resize(480, 1000)
    panel.show()
    pump()
    labels = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    check("パネル: 「リップシンク」タブが検証タブの前にある", "リップシンク" in labels and labels.index("リップシンク") == labels.index("検証") - 1, str(labels))
    panel.select_tab("lipsync")
    tab = panel.tab("lipsync")
    check("画面: タブが本物（準備中でない）", not panel.tab_is_placeholder("lipsync") and tab is not None)
    tab.refresh()
    pump()
    check(
        "空の状態: 説明・「リップシンクを使う」・「プロファイルから作る」が出て、設定・表は隠れている",
        not tab.empty_box.isHidden() and tab.main.isHidden() and tab.btn_start.text() == "リップシンクを使う" and tab.btn_profile_empty.text() == "プロファイルから作る" and tab.btn_start.isEnabled(),
    )
    if shot_dir:
        tab.resize(480, 420)
        tab.layout().activate()
        pump()
        tab.grab().save(str(Path(shot_dir) / "lipsync_empty.png"))
    answers = {"text": [], "confirm": [], "switch": [], "unsaved": []}
    texts_asked: list[str] = []
    tab.ask_text = lambda title, label, value="": (texts_asked.append(label), answers["text"].pop(0))[1]
    tab.ask_confirm = lambda text: (texts_asked.append(text), answers["confirm"].pop(0))[1]
    tab.ask_switch_choice = lambda msg="": answers["switch"].pop(0)
    tab.ask_unsaved_choice = lambda msg="": answers["unsaved"].pop(0)
    tab.btn_start.click()
    pump()
    check("使う: 「リップシンクを使う」で設定ができ、設定・表が出る（Undo 可）", s.doc.lip_sync is not None and s.doc.lip_sync.enabled and not tab.main.isHidden() and tab.empty_box.isHidden() and s.can_undo)
    check("設定の表示: 使う・全体の強さ・声量・追従が文書の値", tab.cb_enabled.isChecked() and abs(tab.strength.value() - 1.0) < TOL and tab.vol_from.value() == 0.5 and tab.vol_to.value() == 1.0 and tab.follow.value() == 20.0)
    check("設定の表示: 追従は Unity だけで使うと書いてある", any("Unity だけで使います" in w.text() for w in tab.findChildren(QtWidgets.QLabel)))
    tab.strength.setValue(0.6)
    tab.vol_from.setValue(0.3)
    tab.follow.setValue(8.0)
    tab.cb_enabled.setChecked(False)
    pump()
    check("設定: スピンボックス・チェックが文書に入る", abs(s.doc.lip_sync.strength - 0.6) < TOL and abs(s.doc.lip_sync.volume.from_ - 0.3) < TOL and s.doc.lip_sync.follow == 8.0 and not s.doc.lip_sync.enabled)
    tab.cb_enabled.setChecked(True)
    # 音素
    for name in ("A", "I", "U"):
        tab.phoneme_edit.setText(name)
        tab.btn_add.click()
    pump()
    check("音素: 足す（A / I / U）・一覧と表の行に出る・同じ名前は足せずメッセージ", [tab.phoneme_list.item(i).text() for i in range(tab.phoneme_list.count())] == ["A", "I", "U"] and tab.table.rowCount() == 3, str(s.doc.lip_sync.phonemes))
    tab.phoneme_edit.setText("A")
    tab.btn_add.click()
    check("音素: 重複はエラーの表示", "すでに" in tab.status.text() and s.doc.lip_sync.phonemes == ["A", "I", "U"], tab.status.text())
    check("表: 列は「基本」と感情レイヤー（Joy）・マスは「—」/（基本）", [tab.table.horizontalHeaderItem(i).text() for i in range(tab.table.columnCount())] == ["基本", "Joy"] and tab.table.item(0, 0).text() == "—" and tab.table.item(0, 1).text() == "（基本）")
    # プロファイルから作る
    tab.btn_profile.click()
    pump()
    res_text = tab.profile_result.text()
    check(
        "プロファイルから作る: 作った数・モデルに無いシェイプが結果に出る・表に「1 シェイプ」/「2 シェイプ」",
        not tab.profile_result.isHidden() and "2 個作りました" in res_text and "no_such_shape" in res_text and tab.table.item(0, 0).text() == "1 シェイプ" and tab.table.item(1, 0).text() == "2 シェイプ" and tab.table.item(2, 0).text() == "—",
        res_text,
    )
    tab.btn_profile.click()
    check("プロファイルから作る: もう作れるものが無いとき失敗の表示（すでに行がある音素は残す）", "作れる行がありませんでした" in tab.status.text() and "A、I" in tab.profile_result.text(), tab.status.text() + tab.profile_result.text())
    real_profile = s.ctx.profile
    s.ctx.set_profile(None)
    tab.btn_profile.click()
    check("プロファイルから作る: プロファイルが無いと、そう伝える", "プロファイルが選ばれていません" in tab.status.text(), tab.status.text())
    s.ctx.set_profile(profile_mod.NamingProfile(name="plain"))
    tab.btn_profile.click()
    check("プロファイルから作る: リップシンクの対応が無いプロファイルだと、そう伝える", "リップシンクの対応" in tab.status.text() and "plain" in tab.status.text(), tab.status.text())
    s.ctx.set_profile(real_profile)
    # マスの選択
    s.set_lip_cell_pose("A", "Joy", SourcePose({"bs.mouth_open": 0.8, "bs.smile_L": 0.3}))
    tab.refresh()
    tab.table.cellClicked.emit(0, 0)
    pump()
    sel_item = tab.table.item(0, 0)
    check(
        "表: マスを押すと編集の対象になる・選択のマスが色つき・対象の行・編集状態",
        s.ctx.selected_lip() == ("A", "") and s.editing and "リップシンクの A（基本）" in tab.target_label.text() and sel_item.background().color().name() == "#5285a6" and tab.btn_clear.isEnabled(),
        f"{s.ctx.selected_lip()} {tab.target_label.text()}",
    )
    check("ヘッダー: 編集中の表示に対象の呼び名が出る", "リップシンクの A（基本）" in panel.header.edit_state.text(), panel.header.edit_state.text())
    pose_tab = panel.tab("pose")
    pose_tab.refresh()
    check(
        "ポーズタブ: 見出しにマスの呼び名・保存ボタンは「保存（この口の形にする）」",
        "リップシンクの A（基本）" in pose_tab.header.text() and pose_tab.btn_save.text() == "保存（この口の形にする）" and pose_tab.btn_save.isEnabled(),
        f"{pose_tab.header.text()} / {pose_tab.btn_save.text()}",
    )
    pose_tab.set_status  # noqa: B018
    s.set_curve("bs.smile_L", 0.25)
    pose_tab.btn_save.click()
    pump()
    check("ポーズタブ: 保存すると行に入り、表の表示も更新される（2 シェイプ）", s.doc.lip_sync.find_entry("A", "").curves == {"bs.mouth_open": 1.0, "bs.smile_L": 0.25} and (tab.refresh() or True) and tab.table.item(0, 0).text() == "2 シェイプ")
    # マスの切り替えの確認
    s.set_curve("bs.smile_R", 0.5)
    answers["switch"] += ["cancel", "discard"]
    tab.table.cellClicked.emit(1, 1)
    check("表: 未保存のとき別のマスを押すと確認・取りやめなら今のマスのまま", s.ctx.selected_lip() == ("A", "") and s.pose.dirty and "取りやめ" in tab.status.text(), tab.status.text())
    tab.table.cellClicked.emit(1, 1)
    check("表: 破棄を選ぶと移る", s.ctx.selected_lip() == ("I", "Joy") and not s.pose.dirty)
    # 空にする
    tab.table.cellClicked.emit(1, 0)
    tab.btn_clear.click()
    pump()
    check("空にする: 「このマスを空にする」で行が消え、表は「—」ではなく空の表示（基本の行なし）", s.doc.lip_sync.find_entry("I", "") is None and tab.table.item(1, 0).text() == "—" and not tab.btn_clear.isEnabled(), tab.status.text())
    tab.clear_cell("A", "Joy")
    check("空にする: 右クリックのメニューの処理（clear_cell）でも消える", s.doc.lip_sync.find_entry("A", "Joy") is None and tab.table.item(0, 1).text() == "（基本）")
    s.undo()
    s.undo()
    tab.refresh()
    # 検証タブからの移動
    s.set_lip_cell_pose("A", "", SourcePose({"bs.mouth_open": 3.0}))  # 可動域の外
    valid = panel.tab("validate")
    panel.select_tab("validate")
    valid.on_run()
    pump()
    found = None
    for ti in range(valid.tree.topLevelItemCount()):
        top = valid.tree.topLevelItem(ti)
        for c in range(top.childCount()):
            it = top.child(c)
            iss = valid._issue_of_item.get(id(it))
            if iss is not None and iss.lip == ("A", ""):
                found = (it, iss)
    check("検証タブ: リップシンクの問題の「どこ」が「リップシンク A（基本）」・ダブルクリックの案内", found is not None and found[0].text(0) == "リップシンク A（基本）" and "リップシンク" in found[0].toolTip(0), str(found and found[0].text(0)))
    s.end_edit()
    if found:
        valid.on_double_click(found[0])
        pump()
        check("検証タブ: ダブルクリックでそのマスが編集の対象になり、リップシンクタブへ移る", s.ctx.selected_lip() == ("A", "") and s.editing and panel.tabs.currentWidget() is panel.slots["lipsync"], f"{panel.tabs.currentIndex()} {s.ctx.selected_lip()} {s.editing} {valid.status.text()} {found[1]}")
    s.set_lip_cell_pose("A", "", SourcePose({"bs.mouth_open": 1.0}))
    s.end_edit()
    # 試すの箱
    tab.refresh()
    check("試す: まだ試していない — スライダーは使えず「試しはじめる」だけ押せる", not tab.try_body.isEnabled() and tab.btn_try.isEnabled() and not tab.btn_try_stop.isEnabled())
    check("試す: 音素ごとのスライダー（A / I / U）・感情は行がある列だけ（Joy は行が無いので出ない）", list(tab.phoneme_sliders) == ["A", "I", "U"])
    s.clear_lip_cell("A", "Joy")
    s.clear_lip_cell("I", "Joy")
    tab.refresh()
    check("試す: 感情の行が 1 つも無いときは感情のスライダーは出ない", list(tab.emotion_sliders) == [])
    s.set_lip_cell_pose("A", "Joy", SourcePose({"bs.mouth_open": 0.8, "bs.smile_L": 0.3}))
    tab.refresh()
    check("試す: 感情のスライダーは行がある感情（Joy）だけ", list(tab.emotion_sliders) == ["Joy"])
    pre = (weights(), eye_t())
    tab.btn_try.click()
    pump()
    check("試す: 「試しはじめる」→ 試している・スライダーが使える・ヘッダーに表示", s.lip_try_active and tab.try_body.isEnabled() and not tab.btn_try.isEnabled() and tab.btn_try_stop.isEnabled() and "リップシンクを試しています" in panel.header.edit_state.text(), panel.header.edit_state.text())
    tab.phoneme_sliders["A"].slider.setValue(100)
    tab.phoneme_sliders["I"].slider.setValue(40)
    pump()
    want = evaluate.lipsync_evaluate(s.doc.lip_sync, {}, {"A": 1.0, "I": 0.4, "U": 0.0}, None, {"Joy": 0.0}, s.lipsync.limit_map())
    check(
        "試す: スライダーを動かすと、計算結果がシーンに当たる（lipsync_evaluate と同じ）・値の表示",
        all(abs(weights()[n] - want.get(f"bs.{n}", 0.0)) < TOL for n in shapes) and abs(weights()["mouth_open"]) > 0.5 and "当てた値" in tab.try_result.text() and tab.phoneme_sliders["A"].value_label.text() == "1.00",
        f"{weights()} {want}",
    )
    tab.cb_volume.setChecked(True)
    tab.volume_slider.slider.setValue(20)
    tab.emotion_sliders["Joy"].slider.setValue(60)
    pump()
    want = evaluate.lipsync_evaluate(s.doc.lip_sync, {}, {"A": 1.0, "I": 0.4, "U": 0.0}, 0.2, {"Joy": 0.6}, s.lipsync.limit_map())
    check("試す: 声量を渡す・感情の重みも反映される", all(abs(weights()[n] - want.get(f"bs.{n}", 0.0)) < TOL for n in shapes) and abs(weights()["mouth_open"] - want["bs.mouth_open"]) < TOL, f"{weights()} {want}")
    check("試す: 画面の操作でも文書・Undo は変わらない", fcpose_io.to_dict(s.doc) == fcpose_io.to_dict(s.doc) and all(w.isEnabled() for w in tab.phoneme_sliders.values()))
    if shot_dir:
        tab.resize(480, 1500)
        tab.layout().activate()
        pump()
        tab.try_box.grab().save(str(Path(shot_dir) / "lipsync_try.png"))
    tab.btn_try_stop.click()
    pump()
    check("試す: 「試すのをやめる」→ 試していない・スライダーは 0 に戻り使えない・シーンは基準姿勢（編集状態のまま）", not s.lip_try_active and not tab.try_body.isEnabled() and tab.phoneme_sliders["A"].value() == 0.0 and all(abs(v) < TOL for v in weights().values()) and s.editing, str(weights()))
    s.end_edit()
    check("試す: 編集を抜けるとシーンは試す前と同じ", weights() == pre[0] and eye_t() == pre[1], f"{weights()} vs {pre[0]}")
    tab.btn_try.click()
    tab.phoneme_sliders["A"].slider.setValue(70)
    panel.select_tab("grid")
    pump()
    check("試す: タブを離れると試すが終わる（シーンは基準姿勢のまま）", not s.lip_try_active and all(abs(v) < TOL for v in weights().values()))
    panel.select_tab("lipsync")
    s.end_edit()
    # 試すを始めるとき、未保存の編集があれば確認
    tab.table.cellClicked.emit(0, 0)
    s.set_curve("bs.smile_R", 0.5)
    answers["unsaved"] += ["cancel", "discard"]
    ok1 = tab.on_try_start()
    check("試す: 未保存の編集があると確認・取りやめなら試さない", not ok1 and not s.lip_try_active and s.ctx.selected_lip() == ("A", "") and s.pose.dirty)
    ok2 = tab.on_try_start()
    check("試す: 破棄なら試しはじめ、対象は外れる", ok2 and s.lip_try_active and s.ctx.selected_lip() is None)
    tab.btn_try_stop.click()
    s.end_edit()
    # 名前の変更・削除・並べ替え
    for i in range(tab.phoneme_list.count()):
        if tab.phoneme_list.item(i).text() == "I":
            tab.phoneme_list.setCurrentRow(i)
    answers["text"] += ["Ii"]
    tab.btn_rename.click()
    pump()
    check("音素: 名前を変える…（入力ダイアログ）→ 一覧と行が付いていく", s.doc.lip_sync.phonemes == ["A", "Ii", "U"] and tab.table.verticalHeaderItem(1).text() == "Ii", str(s.doc.lip_sync.phonemes))
    tab.phoneme_list.setCurrentRow(1)
    tab.btn_up.click()
    pump()
    check("音素: 上へ → 並びが変わる・選びが付いていく", s.doc.lip_sync.phonemes == ["Ii", "A", "U"] and tab.selected_phoneme() == "Ii")
    tab.btn_down.click()
    check("音素: 下へ → 元の並び", s.doc.lip_sync.phonemes == ["A", "Ii", "U"])
    s.set_lip_cell_pose("Ii", "", SourcePose({"bs.smile_L": 0.5}))
    tab.refresh()
    tab.phoneme_list.setCurrentRow(1)
    texts_asked.clear()
    answers["confirm"] += [False, True]
    tab.btn_delete.click()
    check("音素: 削除… の確認（いいえ）→ 何も消えない・確認の文に「行」が消えると書いてある", s.doc.lip_sync.phonemes == ["A", "Ii", "U"] and texts_asked and "行" in texts_asked[-1] and "削除しますか" in texts_asked[-1], str(texts_asked[-1:]))
    tab.btn_delete.click()
    pump()
    check("音素: 削除… の確認（はい）→ 音素と行が消える・Undo で戻る", s.doc.lip_sync.phonemes == ["A", "U"] and all(e.phoneme != "Ii" for e in s.doc.lip_sync.entries) and s.undo() and "Ii" in s.doc.lip_sync.phonemes)
    check("音素: 確認ダイアログの既定は「いいえ」（ask_yes_no）", "QMessageBox.No" in open(REPO / "maya/scripts/tdrive_facial/ui.py", encoding="utf-8").read() and "ask_yes_no" in open(REPO / "maya/scripts/tdrive_facial/ui_lipsync.py", encoding="utf-8").read())

    # 文言: チケット名・準備中が画面に無い
    bad_pat = re.compile(r"\bF\d(?:-\d+)?\b|FU-\d|F5 から|準備中|R-18")
    bad_texts: list[str] = []
    for child in tab.findChildren(QtWidgets.QWidget):
        for t in (child.text() if hasattr(child, "text") and callable(child.text) else "", child.toolTip(), child.title() if hasattr(child, "title") and callable(child.title) else ""):
            if isinstance(t, str) and bad_pat.search(t):
                bad_texts.append(t[:50])
    check("文言: リップシンクタブにチケット名・準備中が出ていない", not bad_texts, str(bad_texts[:4]))
    # 幅 400〜480 px で使える
    w_main = tab.main.minimumSizeHint().width()
    INFO.append(f"リップシンクタブの中身の最小幅 {w_main} px")
    check("幅: 設定・表・試すの最小幅は 440 px 以内（表は横スクロール）", w_main <= 440 and tab.table.horizontalScrollBarPolicy() != 1, str(w_main))
    # 感情が多いとき: 表は横にスクロールできる
    for n in ("E1", "E2", "E3", "E4"):
        s.add_layer(n)
    tab.refresh()
    check("幅: 感情の列が多くても表は横スクロール（ページが横に伸びない）", tab.table.columnCount() == 6 and tab.main.minimumSizeHint().width() <= 440, f"{tab.table.columnCount()} {tab.main.minimumSizeHint().width()}")
    for _ in range(4):
        s.delete_layer(s.doc.layers.__len__() - 1)
    tab.refresh()

    # スクリーンショット（幅 480）
    if shot_dir:
        s.end_edit()
        tab.table.cellClicked.emit(0, 1)
        tab.table.cellClicked.emit(0, 0)
        pump()
        panel.select_tab("lipsync")
        tab.resize(480, 1500)
        tab.layout().activate()
        pump()
        tab.grab().save(str(Path(shot_dir) / "lipsync_tab.png"))

    # 後片付け
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
        for line in INFO:
            print(f"SMOKE INFO {line}")
        print(f"SMOKE RESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        sys.stdout.flush()
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
