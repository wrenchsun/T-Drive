"""FacialController のパース補正（R-34。Maya 側）のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_persp_smoke.py

確かめること:
- ベイク: 空でないキーだけ `FC_<asset>_Persp_K{n}` に焼く（基準姿勢との差分。Neutral は引かない・重み 1 超は丸める）・状態と除外の記録・
  変更あり / 未ベイクの検出（ポーズ変更・キーの削除で番号が詰まる）・変更のあるキーだけベイク・キー 1 個だけベイク・孤立の掃除（空のキー）
- プレビュー: 距離 / 画角の軸で rig = Python（1e-4）、強さ・alpha・enable、無効 / キー無しのとき式が従来と同一、古い印、.fctrack の perspective、キーに焼く
- カメラ: キーの距離（cm）・縦の画角へ合わせる（作業単位が m でも）
- 編集の対象: キーの選択・未保存の確認（保存 / 破棄 / 取りやめ）・保存・Undo・保存中の一時退避・リロードの引き継ぎ
- 画面: パース補正の箱（足す・選ぶ・編集・保存・削除の確認）・プレビューのスライダー・検証タブからの移動・文言
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があれば persp_grid / persp_preview を書く（幅 480 px）。
ファイルはすべて一時フォルダ。
"""

from __future__ import annotations

import math
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
W_TOL = 1e-4


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def spherical(center, yaw_deg: float, pitch_deg: float, dist: float = 60.0):
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (
        center[0] + dist * math.sin(y) * math.cos(p),
        center[1] + dist * math.sin(p),
        center[2] + dist * math.cos(y) * math.cos(p),
    )


def run() -> None:
    import numpy as np
    from maya import cmds
    from maya.api import OpenMaya as om

    import facial_fixture
    from tdrive import project
    from tdrive_facial import export, pose_apply, scene, ui
    from tdrive_facial import preview_rig as pr
    from tdrive_facial import session as S
    from tdrive_facial.core import evaluate, fcpose_io, naming
    from tdrive_facial.core import fctrack as fct
    from tdrive_facial.core import validate as V
    from tdrive_facial.core.model import BoneOffset, SourcePose

    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_persp_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    asset = "mini"
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    S.FacialSession.model_cameras = staticmethod(lambda: ["persp", "pvcam1"])
    cam1 = cmds.camera(name="pvcam1")[0]
    cam1_shape = cmds.listRelatives(cam1, shapes=True)[0]
    head_c = tuple(cmds.xform("head", query=True, worldSpace=True, translation=True))
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 0, 0, 60.0))

    doc0 = facial_fixture.make_doc()
    doc0.bake.delta_threshold = 1e-5
    p_doc = tmp / "src" / "mini.fcpose.json"
    p_doc.parent.mkdir()
    fcpose_io.save(doc0, p_doc)
    ws_curves = ["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"]

    def open_doc() -> None:
        s.close()
        s.open(p_doc)
        s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
        s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")

    def dense(node: str, alias: str, n: int) -> np.ndarray:
        out = np.zeros((n, 3))
        got = scene.read_target_delta(node, alias)
        if got is not None:
            comps, d = got
            out[list(comps)] = d
        return out

    def pose_delta(doc, pose) -> np.ndarray:
        ref = scene.enter_reference_pose([face], extra_joints=["eye_L", "eye_R", "head"])
        try:
            base = scene.read_points(face)
            pose_apply.apply_pose(doc, pose, ref)
            return scene.read_points(face) - base
        finally:
            ref.restore()

    pose_a = SourcePose({"bs.mouth_open": 0.5, "bs.smile_L": 1.4}, {"eye_L": BoneOffset(t=(0.1, 0.0, 0.0))})  # smile_L は 1 超（丸められる）
    pose_b = SourcePose({"bs.brow_up": 0.7, "bs.smile_R": 0.4}, {})
    pose_c = SourcePose({"bs.mouth_open": 0.2}, {})

    # ============================================================ 0. 従来（パース補正なし）の基準
    open_doc()
    s.bake_all()
    node = scene.primary_blend_shape(face)
    nverts = scene.vertex_count(face)
    names_plain = set(scene.target_indices(node))
    check("基準: パース補正が無ければ Persp のシェイプは作られない・要約に出ない", not [n for n in names_plain if "Persp" in n] and "パース補正" not in s.bake_all().summary())
    rep_pre = pr.build_ex(s.doc, cam1)
    text0 = cmds.expression(rep_pre.expression, query=True, string=True)
    sig0 = cmds.getAttr(f"{rep_pre.rig}.tdFacialSignature")
    rig = rep_pre.rig
    check("基準: キー可アトリビュート perspective（0〜1・初期値 = 強さ 1）がある・outPerspective は作らない", cmds.attributeQuery("perspective", node=rig, exists=True) and "perspective" in (cmds.listAttr(rig, keyable=True) or []) and abs(cmds.getAttr(rig + ".perspective") - 1.0) < 1e-9 and not cmds.attributeQuery("outPerspective", node=rig, exists=True))
    check("基準: パース補正なしの式に perspective の記述が増えない", "perspective" not in text0 and "$px" not in text0 and "$dist" not in text0)
    pr.delete(asset)

    # ============================================================ 1. ベイク
    r_on = s.set_perspective_enabled(True)
    r_a = s.add_perspective_key(30.0, pose_a)
    r_b = s.add_perspective_key(50.0, pose_b)
    r_e = s.add_perspective_key(80.0)
    check("キー: 足せる（30 cm にポーズあり・50 cm にポーズあり・80 cm は空）", r_on.ok and r_a.index == 0 and r_b.index == 1 and r_e.index == 2 and len(s.doc.perspective.keys) == 3, str((r_a, r_b, r_e)))
    check("キー: 同じ値・範囲外の値は足せず文書は変わらない", not s.add_perspective_key(30.0).ok and not s.add_perspective_key(-5.0).ok and len(s.doc.perspective.keys) == 3)
    stale_before = s.stale_perspective_keys()
    check("検出: 焼く前は、空でないキーが未ベイク（空のキーは対象外）", stale_before == [0, 1] and s.stale_points() == [], str(stale_before))
    check("検出: STALE_CODES にパース補正の 2 つが入る", {"perspective_key_unbaked", "perspective_key_changed"} <= set(s.STALE_CODES))
    # 出力前の警告（export.py）
    warns = export._bake_warnings(s.doc)
    check("出力前: 未ベイクのパース補正のキーが警告に出る", any("未ベイクのパース補正のキーが 2 個" in w for w in warns), str(warns))
    rep = s.bake_all()
    names = set(scene.target_indices(node))
    persp_names = sorted(n for n in names if "_Persp_" in n)
    check("ベイク: Persp のシェイプはちょうど 1 + 1 本（K0 / K1。空の K2 は作らない）", persp_names == ["FC_mini_Persp_K0", "FC_mini_Persp_K1"], str(persp_names))
    check("ベイク: 報告・要約にパース補正の数が出る", sorted(rep.perspective) == persp_names and rep.perspective_count == 2 and "パース補正 2" in rep.summary(), rep.summary())
    from tdrive_facial import ui_grid

    check("ベイク: 画面の報告にパース補正の行が出る", "パース補正のシェイプ" in ui_grid.GridTab.report_text(rep))
    d0 = dense(node, "FC_mini_Persp_K0", nverts)
    want0 = pose_delta(s.doc, V.clamp_extreme(pose_a))
    d1 = dense(node, "FC_mini_Persp_K1", nverts)
    want1 = pose_delta(s.doc, pose_b)
    check("ベイク: K0 = キーのポーズの基準姿勢からの差分（重み 1 超は丸め・Neutral は引かない。1e-5）", float(np.abs(d0 - want0).max()) < 2e-5 and float(np.abs(want0).max()) > 0.05, f"{np.abs(d0 - want0).max():.2e}")
    check("ベイク: K1 も同じ", float(np.abs(d1 - want1).max()) < 2e-5 and float(np.abs(want1).max()) > 0.05, f"{np.abs(d1 - want1).max():.2e}")
    full = pose_delta(s.doc, pose_a)
    check("ベイク: 丸めた K0 は丸めていないポーズと違う（重み 1.4 が 1 に丸まっている）", float(np.abs(full - d0).max()) > 0.05)
    state = scene.get_bake_state(node)
    excl = scene.get_bake_exclude(node)
    check("ベイク: 状態 = perspective_key_hash・除外の指紋も記録", state.get("FC_mini_Persp_K0") == V.perspective_key_hash(s.doc.perspective.keys[0]) and excl.get("FC_mini_Persp_K1") == V.exclude_signature(s.doc))
    check("ベイク: 直後は変更ありのキー・点が無く、検証にも出ない", s.stale_perspective_keys() == [] and s.stale_points() == [] and not [i for i in s.validate() if i.code.startswith("perspective_") or i.code in ("orphan_target", "baked_morph_missing")], str([(i.code, i.name) for i in s.validate()][:5]))
    check("ベイク: 点のシェイプは変わらない（基準と同じ数）", {n for n in names if "_Persp_" not in n} == names_plain)
    # 値だけを変える → 焼き直し不要
    s.set_perspective_key_value(1, 55.0)
    check("検出: キーの値だけを変えても焼き直しは要らない", s.stale_perspective_keys() == [])
    s.set_perspective_key_value(1, 50.0)
    # ポーズを変える
    s.set_perspective_key_pose(0, SourcePose({"bs.mouth_open": 0.9}, {}))
    check("検出: キーのポーズを変えると「ベイク後に変更」（そのキーだけ）", s.stale_perspective_keys() == [0] and any(i.code == "perspective_key_changed" and i.key == 0 for i in s.validate()), str(s.stale_perspective_keys()))
    check("出力前: ベイク後に変えたパース補正のキーが警告に出る", any("ベイク後に変えたパース補正のキーが 1 個" in w for w in export._bake_warnings(s.doc)))
    rep2 = s.bake_stale()
    check("変更のあるキーだけベイク: そのキーだけを置き換え（点は焼かない・K1 は触らない）", rep2.perspective == ["FC_mini_Persp_K0"] and rep2.replaced == ["FC_mini_Persp_K0"] and not rep2.created and s.stale_perspective_keys() == [], str((rep2.created, rep2.replaced)))
    check("変更のあるキーだけベイク: 焼いた差分が新しいポーズ", float(np.abs(dense(node, "FC_mini_Persp_K0", nverts) - pose_delta(s.doc, SourcePose({"bs.mouth_open": 0.9}, {}))).max()) < 2e-5)
    # 元に戻す: ポーズが戻る → 変更あり（ベイクはエディタ内 Undo に積まれない）
    check("元に戻す: キーのポーズが戻り（A）、ベイク後に変更になる", s.undo() and s.doc.perspective.keys[0].curves == pose_a.curves and s.stale_perspective_keys() == [0], str(s.doc.perspective.keys[0].curves))
    rep_k0 = s.bake_perspective_key(0)
    check("キー 1 個だけベイク: K0 だけ置き換え", rep_k0.perspective == ["FC_mini_Persp_K0"] and len(rep_k0.replaced) == 1 and not rep_k0.created, str((rep_k0.created, rep_k0.replaced)))
    rep_k = s.bake_perspective_key(1)
    check("キー 1 個だけベイク: 報告は K1 だけ", rep_k.perspective == ["FC_mini_Persp_K1"] and len(rep_k.created) + len(rep_k.replaced) == 1, str((rep_k.created, rep_k.replaced)))
    check("キー 1 個だけベイク: 空のキー（K2）を指定しても何も作らない", not s.bake_perspective_key(2).perspective and "FC_mini_Persp_K2" not in scene.target_indices(node))
    s.bake_all()

    # ---- キーの削除（番号が詰まる）→ 再ベイクで正しいシェイプだけ
    keys_before = [k.value for k in s.doc.perspective.keys]
    r_rm = s.remove_perspective_key(0)  # [30 A, 50 B, 80 空] → [50 B, 80 空]
    check("削除: 後ろのキーの番号が詰まる・メッセージにも出る", r_rm.ok and [k.value for k in s.doc.perspective.keys] == [50.0, 80.0] and "再ベイク" in r_rm.message, r_rm.message)
    iss = s.validate()
    check("削除: 詰まったキーは「ベイク後に変更」・空になった番号のシェイプは孤立", s.stale_perspective_keys() == [0] and any(i.code == "orphan_target" and i.name == "FC_mini_Persp_K1" for i in iss), str([(i.code, i.name, i.key) for i in iss if "Persp" in (i.name or "") or i.key is not None]))
    rep3 = s.bake_stale()
    names3 = {n for n in scene.target_indices(node) if "_Persp_" in n}
    check("削除のあと再ベイク: 正しいシェイプだけが残る（K0 = 旧 B。K1 は掃除）", names3 == {"FC_mini_Persp_K0"} and "FC_mini_Persp_K1" in rep3.removed, f"{names3} {rep3.removed}")
    check("削除のあと再ベイク: K0 の差分 = B のポーズ・状態にも K1 が残らない", float(np.abs(dense(node, "FC_mini_Persp_K0", nverts) - pose_delta(s.doc, pose_b)).max()) < 2e-5 and "FC_mini_Persp_K1" not in scene.get_bake_state(node))
    check("削除のあと再ベイク: 検証にキーの問題・孤立が無い", not [i for i in s.validate() if i.code.startswith("perspective_") or i.code in ("orphan_target", "baked_morph_missing")])
    # 後ろのキーを消したとき: 最後のシェイプはその場で消える（stale_morphs）
    s.add_perspective_key(120.0, pose_c)  # [50 B, 80 空, 120 C] → K2
    s.bake_all()
    check("前提: K0 / K2 が焼かれている", {n for n in scene.target_indices(node) if "_Persp_" in n} == {"FC_mini_Persp_K0", "FC_mini_Persp_K2"})
    s.remove_perspective_key(2)
    check("削除（最後のキー）: そのシェイプはすぐシーンから消える", "FC_mini_Persp_K2" not in scene.target_indices(node) and s.stale_perspective_keys() == [])
    # ポーズを空にした → 孤立 → 再ベイクで掃除
    s.clear_perspective_key_pose(0)
    check("ポーズを空にすると、焼いたシェイプは孤立（検証）・再ベイクで消える", any(i.code == "orphan_target" and i.name == "FC_mini_Persp_K0" for i in s.validate()))
    s.bake_all()
    check("ポーズを空にした再ベイク: Persp のシェイプが 0 本", not [n for n in scene.target_indices(node) if "_Persp_" in n])
    s.undo()
    s.bake_all()
    # 除外パターンを変える → 変更あり
    s.add_exclude("curve", "brow")
    check("除外を変えると、パース補正のキーも「ベイク後に変更」", s.stale_perspective_keys() == [0], str(s.stale_perspective_keys()))
    s.set_exclude(curves=[], bones=[])
    check("除外を戻すと変更なし", s.stale_perspective_keys() == [])
    # 1 本の undo チャンク
    s.set_perspective_key_pose(0, pose_a)
    n_chunks_before = len(s._undo)
    cmds.undoInfo(state=True)
    s.bake_all()
    check("ベイク: エディタ内 Undo には積まない・Maya の Undo 1 回でパース補正のシェイプも戻る", len(s._undo) == n_chunks_before, "")
    cmds.undo()
    check("ベイク: Maya の Undo 1 回で戻る（K0 が焼く前の状態 = ベイク後に変更あり）", s.stale_perspective_keys() == [0], str(s.stale_perspective_keys()))
    s.bake_all()

    # ============================================================ 2. プレビュー
    # ---- 距離の軸
    open_doc()
    s.set_perspective_enabled(True)
    s.set_perspective_strength(0.7)
    s.add_perspective_key(30.0, pose_a)
    s.add_perspective_key(60.0, pose_b)
    s.add_perspective_key(120.0)
    s.bake_all()
    doc = s.doc
    rep_p = pr.build_ex(doc, cam1)
    rig = rep_p.rig
    expr = rep_p.expression
    text_on = cmds.expression(expr, query=True, string=True)
    check("プレビュー: Persp を配線（報告の数）・式に記述がある", rep_p.perspective_targets == 2 and "$px" in text_on and "outPerspective" in text_on and "perspective" in text_on)
    check("プレビュー: 報告の数 = 2・要約に出る", rep_p.perspective_targets == 2 and "パース補正 2" in rep_p.summary(), rep_p.summary())
    check("プレビュー: perspective の初期値 = 強さ（0.7）・outPerspective ができる・キーが打てる", abs(cmds.getAttr(rig + ".perspective") - 0.7) < 1e-9 and cmds.attributeQuery("outPerspective", node=rig, exists=True))
    check("プレビュー: has_perspective_targets", pr.has_perspective_targets(asset))
    cmds.setAttr(rig + ".perspective", 1.0)
    worst = 0.0

    def compare(tag: str, **kw) -> float:
        w = pr.current_weights(asset)
        ev = pr.evaluate_python(doc, cam1, **kw)
        dw = max((abs(w[k] - ev["weights"][k]) for k in w), default=0.0)
        miss = set(ev["weights"]) ^ set(w)
        check(f"{tag}: rig = Python（1e-4）", dw < W_TOL and not miss, f"dw={dw:.2e} miss={miss}")
        return dw

    k0, k1 = "FC_mini_Persp_K0", "FC_mini_Persp_K1"
    for dist in (10.0, 30.0, 45.0, 60.0, 90.0, 120.0, 200.0):
        cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, dist))
        for strength in (1.0, 0.5, 0.0):
            cmds.setAttr(rig + ".perspective", strength)
            worst = max(worst, compare(f"距離 {dist:g} cm・強さ {strength:g}"))
        cmds.setAttr(rig + ".perspective", 1.0)
        w = pr.current_weights(asset)
        want = evaluate.perspective_weights([30.0, 60.0, 120.0], dist)
        check(f"距離 {dist:g} cm: K0 / K1 の重み = perspective_weights（{want[0]:.3f} / {want[1]:.3f}）", abs(w[k0] - want[0]) < 1e-5 and abs(w[k1] - want[1]) < 1e-5, f"{w[k0]} {w[k1]}")
        check(f"距離 {dist:g} cm: outPerspective = カメラまでの距離", abs(cmds.getAttr(rig + ".outPerspective") - dist) < 1e-3)
    # alpha / enable / 弱め
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 45.0))
    cmds.setAttr(rig + ".alpha", 0.6)
    compare("alpha 0.6")
    check("alpha: Persp の重みにも alpha が掛かる（0.5 × 0.6）", abs(pr.current_weights(asset)[k0] - 0.5 * 0.6) < 1e-5, str(pr.current_weights(asset)))
    cmds.setAttr(rig + ".alpha", 1.0)
    cmds.setAttr(rig + ".enable", 0)
    check("enable オフ: Persp の重みも 0", all(abs(v) < 1e-9 for k, v in pr.current_weights(asset).items() if "Persp" in k))
    compare("enable オフ")
    cmds.setAttr(rig + ".enable", 1)
    # 角度の端のフェードは掛からない（格子の外の角度でも効く）
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 170, 0, 45.0))
    check("角度が格子の外でも、パース補正は効く（端のフェードは掛けない）", abs(pr.current_weights(asset)[k0] - 0.5) < 1e-5, str(pr.current_weights(asset)[k0]))
    compare("格子の外の角度")
    INFO.append(f"パース補正（距離）: rig = Python の最大誤差 {worst:.2e}")
    # 古い印
    sig1 = cmds.getAttr(f"{rig}.tdFacialSignature")
    doc.perspective.keys[0].value = 31.0
    check("is_stale: キーの値を変えると古い印（式に値を埋めるため）", pr.is_stale(doc))
    doc.perspective.keys[0].value = 30.0
    check("is_stale: 戻すと最新・署名が同じ", not pr.is_stale(doc) and cmds.getAttr(f"{rig}.tdFacialSignature") == sig1)
    doc.perspective.axis = "fov"
    check("is_stale: 軸を変えると古い印", pr.is_stale(doc))
    doc.perspective.axis = "distance"
    # 無効 / キー無しのとき式は従来と同一
    s.set_perspective_enabled(False)
    check("無効: 設定すると式が作り直され、perspective のシェイプは配線されない", s.preview_state() == "live" and not pr.has_perspective_targets(asset), s.preview_state())
    text_off = cmds.expression(pr.build_ex(doc, cam1).expression, query=True, string=True)
    open_doc()
    s.bake_all()
    rep_plain = pr.build_ex(s.doc, cam1)
    check("無効: 式が、パース補正が無いときと完全に同じ（バイト単位）・署名も同じ", text_off == text0 and cmds.getAttr(f"{rep_plain.rig}.tdFacialSignature") == sig0, "differs" if text_off != text0 else "")
    # キー無し（有効だが焼いたシェイプが無い）
    s.set_perspective_enabled(True)
    s.add_perspective_key(30.0, pose_a)  # 焼かない
    text_nobake = cmds.expression(pr.build_ex(s.doc, cam1).expression, query=True, string=True)
    check("キーが焼かれていない（シェイプが無い）とき、式は従来と同一", text_nobake == text0)
    s.add_perspective_key(60.0)  # 空のキーだけ・点だけは焼く
    s.bake_all()
    text_baked = cmds.expression(pr.build_ex(s.doc, cam1).expression, query=True, string=True)
    check("焼いたあとは式にパース補正が入る（1 キーのとき常に 1）", text_baked != text0 and "$px" in text_baked)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 500.0))
    check("キー 1 つ + 空のキー: 30 cm 未満では 1・遠い（空のキー側）では 0", abs(pr.current_weights(asset)["FC_mini_Persp_K0"]) < 1e-9)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 10.0))
    check("キー 30 cm より近いと K0 が 1", abs(pr.current_weights(asset)["FC_mini_Persp_K0"] - 1.0) < 1e-6)
    pr.delete(asset)

    # ---- 画角の軸
    open_doc()
    s.set_perspective_enabled(True)
    s.set_perspective_axis("fov")
    check("軸: fov にできる・不正な軸は失敗", s.doc.perspective.axis == "fov" and not s.set_perspective_axis("zzz").ok)
    s.add_perspective_key(20.0, pose_a)
    s.add_perspective_key(50.0, pose_b)
    check("画角のキー: 0 度・180 度は不正", not s.add_perspective_key(0.0).ok and not s.add_perspective_key(180.0).ok)
    s.bake_all()
    doc = s.doc
    rep_f = pr.build_ex(doc, cam1)
    rig = rep_f.rig
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 70.0))
    worst_f = 0.0
    vfa = cmds.getAttr(cam1_shape + ".verticalFilmAperture")
    for focal in (12.0, 20.0, 35.0, 50.0, 85.0, 200.0):
        cmds.setAttr(cam1_shape + ".focalLength", focal)
        fov = math.degrees(2 * math.atan(vfa * 25.4 * 0.5 / focal))
        worst_f = max(worst_f, compare(f"画角（焦点距離 {focal:g} mm = {fov:.1f}°）"))
        w = pr.current_weights(asset)
        want = evaluate.perspective_weights([20.0, 50.0], fov)
        check(f"画角 {fov:.1f}°: K0 / K1 の重み = perspective_weights", abs(w["FC_mini_Persp_K0"] - want[0]) < 1e-4 and abs(w["FC_mini_Persp_K1"] - want[1]) < 1e-4, f"{w} {want}")
        check(f"画角 {fov:.1f}°: outPerspective = カメラの縦の画角・pr.camera_fov と一致", abs(cmds.getAttr(rig + ".outPerspective") - fov) < 1e-3 and abs(pr.camera_fov(cam1) - fov) < 1e-9)
    INFO.append(f"パース補正（画角）: rig = Python の最大誤差 {worst_f:.2e}")
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 20.0))
    cmds.setAttr(cam1_shape + ".focalLength", 35.0)
    fov35 = pr.camera_fov(cam1)
    check("画角の軸では、カメラの距離は関係しない（距離を変えても重みが同じ）", abs(pr.current_weights(asset)["FC_mini_Persp_K0"] - evaluate.perspective_weights([20.0, 50.0], fov35)[0]) < 1e-4)
    # カメラの入れ替え: レンズの値が追従
    cam2 = cmds.camera(name="pvcam2")[0]
    cam2_shape = cmds.listRelatives(cam2, shapes=True)[0]
    cmds.setAttr(cam2_shape + ".focalLength", 100.0)
    s.preview_set_camera(cam2)
    fov100 = math.degrees(2 * math.atan(cmds.getAttr(cam2_shape + ".verticalFilmAperture") * 25.4 * 0.5 / 100.0))
    check("カメラを入れ替えると、画角が新しいカメラのレンズから決まる", abs(cmds.getAttr(rig + ".outPerspective") - fov100) < 1e-3, str(cmds.getAttr(rig + ".outPerspective")))
    s.preview_set_camera(cam1)
    # 状態・読み出し
    st = s.preview_status()
    check("preview_status: パース補正の値・軸・有無が出る", st.has_perspective and st.perspective_axis == "fov" and st.perspective_value is not None and "画角" in s.perspective_readout(), s.perspective_readout())
    # preview_set_attr
    check("preview_set_attr: perspective を変えられる（キーが無いとき）", abs(s.preview_set_attr("perspective", 0.25) - 0.25) < 1e-9)
    s.set_perspective_strength(0.4)
    check("強さの設定: キーの無い rig の perspective も合わせる", abs(cmds.getAttr(rig + ".perspective") - 0.4) < 1e-9)
    # .fctrack
    out_ft = tmp / "ft"
    res_n = export.export_fctrack(doc, "S010", "mini", 1, 25, out_dir=out_ft)
    check("fctrack: perspective にキーが無ければカーブは出ない", "perspective" not in fct.load(res_n["path"]).curves)
    cmds.setKeyframe(rig, attribute="perspective", time=1, value=1.0)
    cmds.setKeyframe(rig, attribute="perspective", time=13, value=0.25)
    res_k = export.export_fctrack(doc, "S020", "mini", 1, 25, frame_rate=24, out_dir=out_ft)
    pk = fct.load(res_k["path"]).curves.get("perspective", [])
    check("fctrack: perspective のキー → 固定カーブ perspective（秒・値 0〜1）", len(pk) == 2 and abs(pk[0][0]) < 1e-6 and abs(pk[0][1] - 1.0) < 1e-6 and abs(pk[1][0] - 0.5) < 1e-6 and abs(pk[1][1] - 0.25) < 1e-6, str(pk))
    try:
        s.preview_set_attr("perspective", 0.2)
        check("preview_set_attr: キーがある perspective は変えられない", False)
    except S.FacialSessionError:
        check("preview_set_attr: キーがある perspective は変えられない", True)
    cmds.cutKey(rig, attribute="perspective", clear=True)
    # キーに焼く
    cmds.setAttr(rig + ".perspective", 1.0)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 60.0))
    kres = s.preview_bake_to_keys(1, 3, 1, remove_rig=False)
    check("キーに焼く: Persp の weight にもキーが打たれる", any("Persp" in k for k in kres["keyed"]), str(kres["keyed"]))
    s.preview_clear_keys()
    s.preview_build()
    pr.delete(asset)

    # ============================================================ 3. カメラ
    open_doc()
    s.set_perspective_enabled(True)
    s.add_perspective_key(30.0, pose_a)
    s.add_perspective_key(80.0)
    cam3 = cmds.camera(name="placecam")[0]
    cam3_shape = cmds.listRelatives(cam3, shapes=True)[0]
    cmds.setAttr(cam3 + ".translate", 40, 25, -30)
    cmds.setAttr(cam3 + ".rotate", 10, 20, 5)

    def world_dist() -> float:
        return pr.view_distance(s.doc, cam3)

    v = s.camera_to_key(0, cam3)
    yaw, pitch = s.view_angles(cam3)
    check("カメラ: 距離のキー（30 cm）→ 顔の中心から 30 cm（1e-3）・正面（Yaw / Pitch 0）", abs(world_dist() - 30.0) < 1e-3 and abs(yaw) < 1e-3 and abs(pitch) < 1e-3 and v == 30.0, f"{world_dist()} {yaw} {pitch}")
    s.camera_to_key(1, cam3)
    check("カメラ: 80 cm のキー", abs(world_dist() - 80.0) < 1e-3)
    cmds.currentUnit(linear="m")
    cmds.setAttr(cam3 + ".translate", 0.4, 0.25, -0.3)
    s.camera_to_key(0, cam3)
    t = om.MTransformationMatrix(om.MSelectionList().add(cam3).getDagPath(0).inclusiveMatrix()).translation(om.MSpace.kWorld)
    ui_t = cmds.getAttr(cam3 + ".translate")[0]
    check("カメラ: 作業単位が m でも、距離は 30 cm（内部 cm と UI の m の換算）・正面", abs(world_dist() - 30.0) < 1e-3 and abs(s.view_angles(cam3)[0]) < 1e-3, f"{world_dist()} ui={ui_t}")
    check("カメラ: m 単位でも translate は m で書かれる（30 cm 離れた位置 = 0.3 m 級）", abs(math.dist(ui_t, (head_c[0] * 0.01, head_c[1] * 0.01 + 0.0, head_c[2] * 0.01))) < 0.5, str(ui_t))
    cmds.currentUnit(linear="cm")
    s.set_perspective_axis("fov")
    s.set_perspective_key_value(0, 40.0)
    s.set_perspective_key_value(1, 70.0)
    dist_before = (cmds.setAttr(cam3 + ".translate", 0, 30, 90), world_dist())[1]
    v = s.camera_to_key(0, cam3)
    check("カメラ: 画角のキー（40°）→ 縦の画角 40°（1e-6）・フィルムフィットは縦・距離はそのまま・正面", abs(pr.camera_fov(cam3) - 40.0) < 1e-6 and cmds.getAttr(cam3_shape + ".filmFit") == 2 and abs(world_dist() - dist_before) < 1e-3 and abs(s.view_angles(cam3)[0]) < 1e-3 and v == 40.0, f"{pr.camera_fov(cam3)} {cmds.getAttr(cam3_shape + '.filmFit')} {world_dist()} vs {dist_before}")
    s.camera_to_key(1, cam3)
    check("カメラ: 画角 70° も合う", abs(pr.camera_fov(cam3) - 70.0) < 1e-6)
    try:
        pr.set_camera_fov(cam3, 0.0)
        check("カメラ: 画角 0 度は PreviewRigError", False)
    except pr.PreviewRigError:
        check("カメラ: 画角 0 度は PreviewRigError", True)
    try:
        s.camera_to_key(9, cam3)
        check("カメラ: 範囲外のキーは FacialSessionError", False)
    except S.FacialSessionError:
        check("カメラ: 範囲外のキーは FacialSessionError", True)
    s.set_perspective_axis("distance")
    s.set_perspective_key_value(0, 30.0)
    s.set_perspective_key_value(1, 80.0)

    # ============================================================ 4. 編集の対象
    s.close()
    open_doc()
    s.set_perspective_enabled(True)
    s.add_perspective_key(30.0, pose_a)
    s.add_perspective_key(50.0, pose_b)
    s.add_perspective_key(80.0)
    s.bake_all()
    bs = "bs"
    r = s.select_point(1, 2)
    check("対象: まず点（1,2）を選ぶ", r.status == "selected" and s.applied_point == (0, 1, 2) and s.applied_key is None and s.ctx.selected_key() is None)
    r = s.select_key(0)
    check("対象: キー 0 を選ぶと、点の選択は外れ、キーが当たる（applied_key）・編集状態", r.status == "selected" and s.ctx.selection is None and s.ctx.selected_key() == 0 and s.applied_key == 0 and s.applied_point is None and s.editing, f"{r.status} {s.ctx.selection} {s.applied_key}")
    check("対象: バッファ = キーのポーズ・PoseView.key_index・保存済み扱い（未保存でない）", s.pose.curves == {"bs.mouth_open": 0.5, "bs.smile_L": 1.4} and s.pose.view().key_index == 0 and not s.pose.dirty and s.pose.view().can_save)
    check("対象: シーンにキーのポーズが当たる（mouth_open 0.5・smile_L 1.4）", abs(cmds.getAttr(f"{bs}.mouth_open") - 0.5) < 1e-6 and abs(cmds.getAttr(f"{bs}.smile_L") - 1.4) < 1e-6, f"{cmds.getAttr(bs + '.mouth_open')}")
    check("対象: 格子の GridPresenter に選択が無い・対象の呼び名", s.grid.view().selection is None and s.edit_target_label() == "パース補正のキー 1（30 cm）", s.edit_target_label())
    s.set_curve("bs.brow_up", 0.7)
    check("対象: スライダーで編集すると未保存（キーのポーズは変わらない）", s.pose.dirty and "bs.brow_up" not in s.doc.perspective.keys[0].curves and s.has_unsaved_work)
    # 点へ移る: 確認
    r = s.select_point(1, 0)
    check("対象: 未保存のままキー → 点へ移ると needs_confirm（何も変わらない）", r.status == "needs_confirm" and s.ctx.selected_key() == 0 and abs(s.pose.curves.get("bs.brow_up", 0) - 0.7) < 1e-9, r.status)
    r = s.select_point(1, 0, choice="cancel")
    check("対象: 取りやめ → キーのまま・編集中の値が残る", r.status == "cancelled" and s.ctx.selected_key() == 0 and s.pose.dirty)
    r = s.select_point(1, 0, choice="discard")
    check("対象: 破棄 → 点へ移る・キーのポーズは元のまま・キーの対象は外れる", r.status == "selected" and s.ctx.selection == (1, 0) and s.ctx.selected_key() is None and "bs.brow_up" not in s.doc.perspective.keys[0].curves and s.applied_point == (0, 1, 0), r.status)
    # 点 → キー（未保存の点の編集 → 保存）
    s.set_curve("bs.mouth_open", 0.33)
    r = s.select_key(0)
    check("対象: 未保存の点の編集があるとき、キーを選ぶと needs_confirm", r.status == "needs_confirm" and s.ctx.selection == (1, 0))
    r = s.select_key(0, choice="save")
    check("対象: 保存 → 点に書かれてキーへ移る", r.status == "selected" and r.saved and s.ctx.selected_key() == 0 and abs(s.doc.layers[0].points[(1, 0)].pose.curves.get("bs.mouth_open", 0) - 0.33) < 1e-6, str(r.status))
    # キー → キー（保存）
    s.set_curve("bs.brow_up", 0.7)
    s.set_bone("eye_R", BoneOffset(t=(0.0, 0.1, 0.0)))
    r = s.select_key(1)
    check("対象: 未保存のキー 0 の編集があるとき、キー 1 を選ぶと needs_confirm", r.status == "needs_confirm" and s.ctx.selected_key() == 0)
    r = s.select_key(1, choice="save")
    k0 = s.doc.perspective.keys[0]
    check("対象: 保存 → キー 0 のポーズに書かれ（original + 編集）、キー 1 へ移る", r.status == "selected" and r.saved and s.ctx.selected_key() == 1 and abs(k0.curves.get("bs.brow_up", 0) - 0.7) < 1e-6 and k0.curves.get("bs.mouth_open") == 0.5 and "eye_R" in k0.bones, str((r.status, k0.curves)))
    check("対象: 保存したキーはベイク後に変更あり（検証）", 0 in s.stale_perspective_keys() and s.pose.view().key_index == 1 and s.pose.curves == {"bs.brow_up": 0.7, "bs.smile_R": 0.4})
    # 同じキーの選び直しは編集中の値を保つ
    s.set_curve("bs.smile_R", 0.9)
    r = s.select_key(1)
    check("対象: 同じキーを選び直しても編集中の値を保つ", r.status == "same_point" and abs(s.pose.curves["bs.smile_R"] - 0.9) < 1e-9 and r.camera_jump)
    # レイヤー切り替えはキーの編集に影響しない
    r = s.set_active_layer(1)
    check("対象: レイヤーを切り替えても、キーの編集中の値は確認なしでそのまま", r.status == "selected" and s.ctx.selected_key() == 1 and abs(s.pose.curves["bs.smile_R"] - 0.9) < 1e-9)
    s.set_active_layer(0)
    # 保存（save_point）= キーのポーズへ
    res = s.save_point()
    check("保存: save_point はキー 1 のポーズへ書く・Undo 1 回で戻る", res.ok and abs(s.doc.perspective.keys[1].curves["bs.smile_R"] - 0.9) < 1e-9 and not s.pose.dirty)
    check("保存: Undo でキーのポーズが戻り、編集中の値も読み直される（キーを選んだまま）", s.undo() and abs(s.doc.perspective.keys[1].curves["bs.smile_R"] - 0.4) < 1e-9 and s.ctx.selected_key() == 1 and abs(s.pose.curves["bs.smile_R"] - 0.4) < 1e-9, str(s.pose.curves))
    s.redo()
    # 空のポーズを保存 → 空のキー（消えない）
    s.pose.zero()
    s.save_point()
    check("保存: 全部 0 を保存してもキーは消えず空のキー（補正なし）になる", len(s.doc.perspective.keys) == 3 and s.doc.perspective.keys[1].is_empty())
    s.undo()
    # 保存中の一時退避
    s.set_curve("bs.brow_up", 0.55)
    s.select_key(0)  # needs_confirm 回避のため先に捨てる
    s.select_key(0, choice="discard")
    s.set_curve("bs.brow_up", 0.55)
    buf_before = dict(s.pose.pose_to_apply().curves)
    w_edit = {n: round(cmds.getAttr(f"{bs}.{n}"), 6) for n in ("mouth_open", "smile_L", "brow_up")}
    f = tmp / "saved_key.ma"
    cmds.file(rename=str(f))
    S.install_scene_callbacks()
    cmds.file(save=True, type="mayaAscii")
    check("保存中: 編集状態のまま・同じキー・編集中の値が保たれシーンに当たっている", s.editing and s.applied_key == 0 and dict(s.pose.pose_to_apply().curves) == buf_before and {n: round(cmds.getAttr(f"{bs}.{n}"), 6) for n in w_edit} == w_edit, f"{s.editing} {s.applied_key}")
    text = f.read_text(encoding="utf-8", errors="replace")
    m_bs = re.search(r'createNode blendShape -n "bs";(.*?)(?=\ncreateNode|\Z)', text, re.S)
    sect = m_bs.group(1) if m_bs else ""
    check("保存中: 保存したファイルには編集中のポーズが入らない（blendShape の重みが基準 0・ファイルの書き込み時は退避中）", not re.search(r'setAttr ".w\[\d\]" 0\.5\b', sect) and not re.search(r'setAttr ".w\[\d\]" 1\.4\b', sect), sect[:300])
    check("保存中: 保存のあとシーンの変更フラグは立たない", not cmds.file(query=True, modified=True))
    S.remove_scene_callbacks()
    # リロードの引き継ぎ（export_state / import_state）
    state = s.export_state()
    s2 = S.FacialSession()
    s2.import_state(state)
    check("リロード: 編集の対象のキーと編集中の値が残る", s2.ctx.selected_key() == 0 and s2.ctx.selection is None and abs(s2.pose.curves.get("bs.brow_up", 0) - 0.55) < 1e-9 and s2.pose.dirty, f"{s2.ctx.selected_key()} {s2.pose.curves}")
    check("リロード: 文書も同じ（キーの値・ポーズ）", fcpose_io.to_dict(s2.doc) == fcpose_io.to_dict(s.doc))
    s2.close()
    # 削除・Undo と対象
    s.select_key(0, choice="discard")
    s.set_curve("bs.brow_up", 0.3)
    r_rm = s.remove_perspective_key(0)
    check("削除: 選んでいたキーを消すと、編集の対象は外れる・シーンは基準姿勢へ", r_rm.ok and s.ctx.selected_key() is None and s.applied_key is None and not s.pose.dirty and abs(cmds.getAttr(f"{bs}.mouth_open")) < 1e-9, str(s.ctx.key_target))
    s.undo()
    check("削除を Undo: キーが戻る（対象は戻らない）", len(s.doc.perspective.keys) == 3 and s.ctx.selected_key() is None)
    s.select_key(2, choice="discard")
    s.remove_perspective_key(0)
    check("削除: 前のキーを消すと、選んでいるキーの番号が詰まって同じキーのまま（2 → 1）・編集中の値も残る", s.ctx.selected_key() == 1 and s.doc.perspective.keys[1].value == 80.0)
    s.undo()
    sk = s.ctx.selected_key()
    check("Undo: 対象のキーは範囲内か外れており、編集中の値は対象のキーのポーズと同じ（食い違わない）", sk is None or (sk < len(s.doc.perspective.keys) and s.pose.curves == s.doc.perspective.keys[sk].curves), str((sk, s.pose.curves)))
    s.end_edit(quiet=True)

    # ============================================================ 5. 画面
    s.close()
    open_doc()
    s.set_perspective_enabled(True)
    panel = ui.FacialPanel()
    panel.resize(480, 1500)
    grid = panel.tab("grid")
    pose = panel.tab("pose")
    valid = panel.tab("validate")
    pv = grid.preview
    pg = grid.persp
    panel.select_tab("grid")
    pump()
    # 値を聞くダイアログの差し替え
    asked: list = []
    answers: list = []
    pg.ask_value = lambda title, label, value, lo, hi, decimals=1: (asked.append((title, label, value)), answers.pop(0) if answers else value)[1]
    confirms: list = []
    confirm_answers: list = []
    pg.ask_confirm = lambda text: (confirms.append(text), confirm_answers.pop(0))[1]
    choices: list = []
    pg.ask_unsaved_choice = lambda msg: choices.pop(0)
    check("箱: 見出し・使う・軸・強さ・一覧・ボタン 3 つ・畳める", isinstance(pg, QtWidgets.QWidget) and pg.cb_enabled.isChecked() and pg.axis_combo.count() == 2 and pg.strength.maximum() == 1.0 and pg.tree.columnCount() == 3 and pg.btn_add.text() == "キーを足す" and pg.btn_value.text() == "値を変える…" and pg.btn_delete.text() == "削除…" and "パース補正" in pg.toggle.text(), pg.toggle.text())
    check("箱: キーが無いとき一覧は空・値を変える / 削除は使えない・案内が出る", pg.tree.topLevelItemCount() == 0 and not pg.btn_value.isEnabled() and not pg.btn_delete.isEnabled() and "キーがありません" in pg.target_label.text())
    # 足す
    answers.append(30.0)
    pg.on_add()
    pump()
    check("足す: 提案値（30）から聞き、キーが足されて編集の対象になる", asked[-1][2] == 30.0 and len(s.doc.perspective.keys) == 1 and s.ctx.selected_key() == 0 and pg.tree.topLevelItemCount() == 1, str(asked))
    check("足す: 一覧の行（値と単位・ポーズなし・ベイクの状態）・選択・ツールチップ", "30 cm" in pg.tree.topLevelItem(0).text(0) and "なし" in pg.tree.topLevelItem(0).text(1) and pg.selected_index() == 0 and "シェイプ番号 K0" in pg.tree.topLevelItem(0).toolTip(0), pg.tree.topLevelItem(0).text(0))
    check("足す: 箱の下に「編集の対象: パース補正のキー 1（30 cm）」が出る", "編集の対象:" in pg.target_label.text() and "パース補正のキー 1（30 cm）" in pg.target_label.text(), pg.target_label.text())
    pose.refresh()
    pump()
    check("ポーズタブ: 見出しが「編集の対象: パース補正のキー 1（30 cm）」・保存ボタンの文言・編集できる", "編集の対象" in pose.header.text() and "パース補正のキー 1（30 cm）" in pose.header.text() and "このキーのポーズにする" in pose.btn_save.text() and pose.body.isEnabled(), pose.header.text())
    panel.header.refresh()
    check("ヘッダー: 編集中の表示に「編集の対象: パース補正のキー 1（30 cm）」が出る", "編集の対象: パース補正のキー 1（30 cm）" in panel.header.edit_state.text(), panel.header.edit_state.text())
    check("グリッド: 点の選択が外れている（キャンバスに選択なし）", s.ctx.selection is None and grid.canvas.isEnabled())
    # ポーズを編集して保存
    s.set_curve("bs.mouth_open", 0.6)
    s.set_curve("bs.smile_L", 0.3)
    pose.flush()
    pose.refresh()
    check("ポーズタブ: 未保存の印", "未保存" in pose.header.text())
    pose.btn_save.click()
    pump()
    pg.refresh()
    check("ポーズタブ: 保存すると、キーのポーズに入る（シェイプ 2 本）・一覧の行が「あり」・未ベイク", s.doc.perspective.keys[0].curves == {"bs.mouth_open": 0.6, "bs.smile_L": 0.3} and "あり" in pg.tree.topLevelItem(0).text(1) and ("未ベイク" in pg.tree.topLevelItem(0).text(2) or "変更あり" in pg.tree.topLevelItem(0).text(2)), pg.tree.topLevelItem(0).text(1) + pg.tree.topLevelItem(0).text(2))
    # 2 つ目のキー
    answers.append(60.0)
    pg.on_add()
    pump()
    s.set_curve("bs.brow_up", 0.8)
    pose.btn_save.click()
    pump()
    answers.append(120.0)
    pg.on_add()  # 空のキーのまま
    pump()
    check("足す: 3 つのキー・最後のキーは空（補正なし）・提案値は重ならない", len(s.doc.perspective.keys) == 3 and s.doc.perspective.keys[2].is_empty() and "補正なし" in pg.tree.topLevelItem(2).text(1), str([k.value for k in s.doc.perspective.keys]))
    # 重ならない値・不正な値
    answers.append(30.0)
    pg.on_add()
    check("足す: 同じ値は足せない（メッセージ）", len(s.doc.perspective.keys) == 3 and "すでにあります" in pg.status.text(), pg.status.text())
    # ベイク（画面）
    grid.btn_bake_all.click()
    pump()
    pg.refresh()
    check("ベイク（全部）: パース補正のキーも焼かれ、一覧が「ベイク済み」・報告に出る", "ベイク済み" in pg.tree.topLevelItem(0).text(2) and "パース補正のシェイプ" in grid.report.toPlainText(), pg.tree.topLevelItem(0).text(2))
    # 選択（クリック）→ 点の選択が外れる / 点を選ぶとキーの選択が外れる
    pg.on_item_clicked(pg.tree.topLevelItem(1), 0)
    pump()
    check("選択: 2 行目をクリックすると、キー 2 が編集の対象になる・カメラも動かす（オン）ので 60 cm の位置へ", s.ctx.selected_key() == 1 and abs(pr.view_distance(s.doc, "persp") - 60.0) < 1e-2, f"{s.ctx.selected_key()} {pr.view_distance(s.doc, 'persp')}")
    grid.on_cell_clicked(1, 2)
    pump()
    pg.refresh()
    check("選択: 格子の点をクリックすると、キーの選択が外れる（一覧の選択も外れる）", s.ctx.selected_key() is None and s.ctx.selection == (1, 2) and pg.selected_index() is None)
    # 未保存の編集 → キーの選択で確認
    s.set_curve("bs.mouth_open", 0.77)
    choices.append("cancel")
    pg.on_item_clicked(pg.tree.topLevelItem(0), 0)
    check("選択: 未保存の点の編集があるとき、キーをクリックすると確認（取りやめ → 点のまま）", s.ctx.selection == (1, 2) and s.ctx.selected_key() is None and "取りやめ" in pg.status.text(), pg.status.text())
    choices.append("discard")
    pg.on_item_clicked(pg.tree.topLevelItem(0), 0)
    check("選択: 破棄 → キー 1 へ移る", s.ctx.selected_key() == 0 and s.ctx.selection is None)
    # 値を変える
    answers.append(35.0)
    pg.on_set_value()
    check("値を変える: キー 1 の値が 35 になる・焼き直しは要らない", s.doc.perspective.keys[0].value == 35.0 and 0 not in s.stale_perspective_keys(), pg.status.text())
    answers.append(60.0)
    pg.on_set_value()
    check("値を変える: 重なる値は拒否（メッセージ）", s.doc.perspective.keys[0].value == 35.0 and "すでにあります" in pg.status.text(), pg.status.text())
    answers.append(None)
    pg.on_set_value()
    check("値を変える: キャンセルでは何も変わらない", s.doc.perspective.keys[0].value == 35.0 and "取りやめ" in pg.status.text())
    answers.append(30.0)
    pg.on_set_value()
    # 使う / 軸 / 強さ
    pg.cb_enabled.setChecked(False)
    pump()
    check("使う: オフにすると文書に入る（キーは残る）", s.doc.perspective.enabled is False and len(s.doc.perspective.keys) == 3)
    pg.cb_enabled.setChecked(True)
    pg.strength.setValue(0.35)
    pump()
    check("強さ: スピンボックスで文書に入る（Undo 可）", abs(s.doc.perspective.strength - 0.35) < 1e-9 and s.can_undo)
    pg.axis_combo.setCurrentIndex(1)
    pg.on_axis_activated()
    check("軸: 画角にすると文書に入り、値を見直すメッセージが出る", s.doc.perspective.axis == "fov" and "見直して" in pg.status.text(), pg.status.text())
    check("軸: 画角のとき、一覧の値の単位が「度」", "度" in pg.tree.topLevelItem(0).text(0), pg.tree.topLevelItem(0).text(0))
    pg.axis_combo.setCurrentIndex(0)
    pg.on_axis_activated()
    pg.strength.setValue(1.0)
    # 削除（確認・既定は「いいえ」）
    pg.on_item_clicked(pg.tree.topLevelItem(1), 0)
    confirm_answers.append(False)
    pg.on_delete()
    check("削除: 確認で「いいえ」なら消えない・確認文に番号が詰まる注意が入る", len(s.doc.perspective.keys) == 3 and "詰まります" in confirms[-1] and "削除しますか" in confirms[-1] and "キー 2（60 cm）" in confirms[-1], confirms[-1])
    check("削除: 確認ダイアログの既定は「いいえ」（ask_yes_no）", "QMessageBox.No" in open(REPO / "maya/scripts/tdrive_facial/ui.py", encoding="utf-8").read())
    confirm_answers.append(True)
    pg.on_delete()
    pump()
    check("削除: 「はい」で消え、後ろのキーの番号が詰まる・対象が外れる・再ベイクのメッセージ", len(s.doc.perspective.keys) == 2 and s.ctx.selected_key() is None and "再ベイク" in pg.status.text(), pg.status.text())
    check("削除: 一覧の行が 2 本になる", pg.tree.topLevelItemCount() == 2, str(pg.tree.topLevelItemCount()))
    check("削除: 古いシェイプ（空になった番号）が孤立として残り、ベイク（変更のある点）で掃除される前提", any(i.code == "orphan_target" and "_Persp_K" in i.name for i in s.validate()) or s.stale_perspective_keys() == [])
    grid.btn_bake_stale.click()
    pump()
    pg.refresh()
    check("ベイク（変更のある点）: 古いシェイプが掃除され（削除 1）、パース補正のキーに変更ありが残らない", s.stale_perspective_keys() == [] and "削除 1" in grid.report.toPlainText() and not [i for i in s.validate() if i.code == "orphan_target"], grid.report.toPlainText())
    # ベイク（この点）= キー 1 個
    pg.on_item_clicked(pg.tree.topLevelItem(0), 0)
    s.set_curve("bs.mouth_open", 0.45)
    s.save_point()
    check("ベイク（この点）: キーを選んでいるとき使え、そのキーだけ焼く", grid.btn_bake_point.isEnabled())
    grid.btn_bake_point.click()
    pump()
    check("ベイク（この点）: 報告はパース補正 1 本", "パース補正のシェイプ（FC_…_Persp_K）: 1 個" in grid.report.toPlainText(), grid.report.toPlainText())
    # 検証タブ: 問題の行の「どこ」・ダブルクリックでキーへ
    s.set_perspective_key_pose(1, SourcePose({"bs.brow_up": 0.2}, {}))  # 空だったキーにポーズを入れた = 未ベイク
    s.end_edit(quiet=True)
    s.select_point(1, 2)
    valid.on_run()
    pump()
    found = None
    for top_i in range(valid.tree.topLevelItemCount()):
        top = valid.tree.topLevelItem(top_i)
        for c in range(top.childCount()):
            it = top.child(c)
            iss = valid._issue_of_item.get(id(it))
            if iss is not None and iss.key == 1 and iss.code in ("perspective_key_changed", "perspective_key_unbaked"):
                found = (it, iss)
    check("検証タブ: パース補正のキーの問題が出る（場所の列に「パース補正 キー 2」）・ダブルクリックの案内", found is not None and "パース補正 キー 2" in found[0].text(0) and "パース補正のキー" in found[0].toolTip(0), str(found and found[0].text(0)))
    if found:
        valid.on_double_click(found[0])
        pump()
        check("検証タブ: ダブルクリックでそのキーが編集の対象になる（点の選択は外れる）", s.ctx.selected_key() == 1 and s.ctx.selection is None and s.editing)
    # 箱の畳み
    pg.toggle.setChecked(False)
    pump()
    check("畳める: 閉じると箱の中が隠れ、見出しに概要が残る", not pg.box.isVisible() or pg.box.isHidden(), pg.toggle.text())
    pg.toggle.setChecked(True)
    pump()
    # プレビュー
    s.end_edit(quiet=True)
    s.select_point(1, 2)
    s.end_edit(quiet=True)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 0, 0, 45.0))
    s.preview_build(cam1)
    pv.refresh()
    pump()
    rig = s.preview_rig_node()
    check("プレビュー: パース補正のスライダー（使える・初期値 = 強さ）・ツールチップ", pv.persp_slider.isEnabled() and "パース補正" in pv.persp_label.text() and abs(cmds.getAttr(rig + ".perspective") - 1.0) < 1e-9 and "広角" in pv.persp_slider.toolTip(), pv.persp_value.text())
    pv.persp_slider.setValue(40)
    check("プレビュー: スライダーで rig の perspective が動く", abs(cmds.getAttr(rig + ".perspective") - 0.4) < 1e-6 and "0.40" in pv.persp_value.text(), pv.persp_value.text())
    pv.tick()
    check("プレビュー: 角度の表示の横に今の軸の値（距離 45 cm）が出る", "距離 45 cm" in pv.angle_label.text(), pv.angle_label.text())
    cmds.setKeyframe(rig, attribute="perspective", time=1, value=1.0)
    cmds.setKeyframe(rig, attribute="perspective", time=5, value=0.0)
    pv.refresh()
    check("プレビュー: キーがあると「キーあり」と出て動かせない", not pv.persp_slider.isEnabled() and "キーあり" in pv.persp_value.text(), pv.persp_value.text())
    cmds.cutKey(rig, attribute="perspective", clear=True)
    s.set_perspective_enabled(False)
    s.preview_build(cam1)
    pv.refresh()
    check("プレビュー: パース補正のシェイプを配線していないとき、スライダーは使えず理由が出る", not pv.persp_slider.isEnabled() and "シェイプなし" in pv.persp_value.text(), pv.persp_value.text())
    s.set_perspective_enabled(True)
    s.preview_build(cam1)
    pv.refresh()

    # 文言: チケット名・準備中が画面に無い
    bad_pat = re.compile(r"\bF\d(?:-\d+)?\b|FU-\d|F5 から|準備中")
    bad_texts: list[str] = []
    for key in ("setup", "grid", "pose", "shapes", "layers", "validate", "export"):
        w = panel.tab(key)
        for child in w.findChildren(QtWidgets.QWidget):
            for t in (child.text() if hasattr(child, "text") and callable(child.text) else "", child.toolTip(), child.title() if hasattr(child, "title") and callable(child.title) else ""):
                if isinstance(t, str) and bad_pat.search(t):
                    bad_texts.append(f"{key}: {t[:50]}")
    check("文言: どのタブにもチケット名・準備中が出ていない", not bad_texts, str(bad_texts[:4]))

    # 幅 400〜480 px で使える（横スクロールなし = 中身の最小幅が収まる）
    body = grid.scroll.widget()
    widths = {n: getattr(grid, n).minimumSizeHint().width() for n in ("canvas", "persp", "preview")}
    INFO.append(f"グリッドタブの中身の最小幅 {body.minimumSizeHint().width()} px（canvas / 箱 / プレビュー: {widths}）")
    check("幅: パース補正の箱の最小幅は 400 px 以内", pg.minimumSizeHint().width() <= 400, str(pg.minimumSizeHint().width()))

    # スクリーンショット（幅 480）
    if shot_dir:
        s.end_edit(quiet=True)
        pg.on_item_clicked(pg.tree.topLevelItem(0), 0)
        pump()
        panel.select_tab("grid")
        grid.resize(480, 1700)
        grid.layout().activate()
        grid.scroll.widget().adjustSize()
        pump()
        grid.grab().save(str(Path(shot_dir) / "persp_grid.png"))
        pv.setEnabled(True)
        pv.resize(480, 460)
        pv.layout().activate()
        pump()
        pv.grab().save(str(Path(shot_dir) / "persp_preview.png"))

    # 後片付け
    pr.delete(asset)
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
