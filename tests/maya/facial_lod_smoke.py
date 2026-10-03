"""FacialController の LOD1 以降のメッシュへのベイク（F5-7）のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_lod_smoke.py

合成モデル: 顔（頂点 302）+ LOD1（頂点 72・自分の blendShape に同じ名前のターゲット 4 本）+ LOD2（頂点 20・ターゲット 2 本だけ）+ LOD3（blendShape なし）。
確かめること:
- セッション: LOD のメッシュを足す・外す・番号を変える（Undo / やり直し）・失敗では変わらない・保存の往復
- ベイク: 同じ名前の FC_（Ex も）が各メッシュの blendShape にでき、差分 = そのメッシュにポーズを当てた差分（感情は Neutral を引く）/
  blendShape の無いメッシュはスキンより前に tdFacial_<mesh> を作る / 対応するターゲットが無いシェイプは動かない / 記録・孤立の掃除・
  1 点だけ・部位別の強さ・パース補正のキー / シーンにないメッシュでは止まる
- 検証: 対応するターゲットの警告・メッシュが無いエラー・LOD に補正が足りない警告
- プレビュー: 顔と LOD に同じ重みを配線 / 出力: FBX に LOD メッシュ + FC_（FBX を取り込み直して確認）
- 画面: セットアップタブ「LOD のメッシュ」の表・番号の変更・外す
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があれば setup_parts_lod.png（対象のメッシュ + 部位別の強さ。幅 480 px）などを書く。
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
    from maya import cmds, mel

    import facial_fixture
    from tdrive import project
    from tdrive_facial import bake as bake_mod
    from tdrive_facial import export, pose_apply, scene, ui
    from tdrive_facial import preview_rig as pr
    from tdrive_facial import session as S
    from tdrive_facial import ui_export
    from tdrive_facial.core import fcpose_io, naming
    from tdrive_facial.core import validate as V
    from tdrive_facial.core.model import BoneOffset, SourcePose

    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_lod_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    lod1_info = facial_fixture.add_lod1("mini_face_LOD1", "bs_lod1")  # 頂点 72・自分の blendShape（同じ名前のターゲット 4 本）
    lod2_info = facial_fixture.add_lod1("mini_face_LOD2", "bs_lod2", divisions=(6, 4), aliases=("mouth_open", "smile_L"))  # 頂点 20・ターゲットは 2 本だけ
    lod3 = cmds.polySphere(name="mini_face_LOD3", radius=8, subdivisionsX=5, subdivisionsY=4, axis=(0, 1, 0))[0]  # blendShape なし・スキンのみ
    cmds.move(0, 12, 0, lod3, absolute=True)
    cmds.makeIdentity(lod3, apply=True, translate=True)
    cmds.skinCluster("root", "head", lod3, toSelectedBones=True, maximumInfluences=2, name="lod3_skin")
    face = scene.resolve_mesh("mini_face")
    lod1 = scene.resolve_mesh("mini_face_LOD1")
    lod2 = scene.resolve_mesh("mini_face_LOD2")
    lod3 = scene.resolve_mesh("mini_face_LOD3")
    asset = "mini"
    n_face, n1, n2, n3 = (scene.vertex_count(m) for m in (face, lod1, lod2, lod3))
    check("前提: LOD の頂点数が顔と違う（302 / 72 / 20 / 17 前後）", len({n_face, n1, n2, n3}) == 4 and n1 < n_face, str((n_face, n1, n2, n3)))

    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    S.FacialSession.model_cameras = staticmethod(lambda: ["persp", "pvcam1"])
    cam1 = cmds.camera(name="pvcam1")[0]
    head_c = tuple(cmds.xform("head", query=True, worldSpace=True, translation=True))
    cmds.setAttr(cam1 + ".translate", head_c[0] + 15, head_c[1] + 8, head_c[2] + 55)
    cmds.setAttr(cam1 + ".rotate", -8, 15, 0)

    doc0 = facial_fixture.make_doc()
    doc0.bake.delta_threshold = 1e-5
    doc0.layers[0].points[(1, 2)].pose.curves["bs.mouth_open"] = 1.5  # 重み 1 超 = 誇張用の _Ex も焼かれる
    doc0.limits = {"bs.mouth_open": (0.0, 2.0)}
    p_doc = tmp / "src" / "mini.fcpose.json"
    p_doc.parent.mkdir()
    fcpose_io.save(doc0, p_doc)
    ws_curves = ["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"]
    s.open(p_doc)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")

    def dense(node: str, alias: str, mesh: str) -> np.ndarray:
        out = np.zeros((scene.vertex_count(mesh), 3))
        got = scene.read_target_delta(node, alias, scene.geometry_index(node, mesh))
        if got is not None:
            comps, d = got
            out[list(comps)] = d
        return out

    def pose_delta(doc, pose, mesh: str, with_all=True) -> np.ndarray:
        """基準姿勢で、ポーズを当てたときの mesh の頂点の動き（独立した期待値。顔と LOD を同時に基準姿勢へ）。"""
        ref = scene.enter_reference_pose([face, lod1, lod2, lod3], extra_joints=["eye_L", "eye_R", "head"])
        try:
            base = scene.read_points(mesh)
            pose_apply.apply_pose(doc, pose, ref)
            return scene.read_points(mesh) - base
        finally:
            ref.restore()

    def err(a, b) -> float:
        return float(np.abs(a - b).max())

    # ============================================================ 1. セッション API
    r1 = s.add_lod_mesh("mini_face_LOD1")
    check("API: LOD のメッシュを足せる（番号は次の番号 = 1）・メッセージに LOD 番号", r1.ok and s.lod_meshes() == [("mini_face_LOD1", 1)] and "LOD1" in r1.message, str((r1, s.lod_meshes())))
    check("API: 次の LOD 番号は最大 + 1", s.next_lod_number() == 2)
    bad = [s.add_lod_mesh("mini_face"), s.add_lod_mesh("mini_face_LOD1"), s.add_lod_mesh("nonexistent_mesh"), s.add_lod_mesh("mini_face_LOD2", 0), s.add_lod_mesh("mini_face_LOD2", -2), s.add_lod_mesh("mini_face_LOD2", True), s.set_lod_number("mini_face_LOD1", 0), s.set_lod_number("zzz", 2), s.remove_lod_mesh("zzz")]
    check("API: 顔・登録済み・無いメッシュ・LOD 0 未満・存在しない行は失敗して一覧は変わらない", not any(b.ok for b in bad) and s.lod_meshes() == [("mini_face_LOD1", 1)], str([b.code for b in bad]))
    s.add_lod_mesh("mini_face_LOD2")
    s.add_lod_mesh("mini_face_LOD3", 5)
    check("API: 足した順に並び、番号を指定できる", s.lod_meshes() == [("mini_face_LOD1", 1), ("mini_face_LOD2", 2), ("mini_face_LOD3", 5)], str(s.lod_meshes()))
    s.set_lod_number("mini_face_LOD3", 3)
    check("API: LOD 番号を変えられる", s.lod_meshes()[2] == ("mini_face_LOD3", 3))
    check("API: 元に戻す・やり直し", s.undo() and s.lod_meshes()[2] == ("mini_face_LOD3", 5) and s.redo() and s.lod_meshes()[2] == ("mini_face_LOD3", 3))
    check("API: target_meshes は 顔 → LOD の順", [scene.short_name(m) for m in s.target_meshes()] == ["mini_face", "mini_face_LOD1", "mini_face_LOD2", "mini_face_LOD3"])
    s.remove_lod_mesh("mini_face_LOD3")
    check("API: 外せる・元に戻すで戻る", s.lod_meshes() == [("mini_face_LOD1", 1), ("mini_face_LOD2", 2)] and s.undo() and len(s.lod_meshes()) == 3 and s.redo() and len(s.lod_meshes()) == 2)
    check("API: set_target で顔を LOD のメッシュに変えると LOD から外れる", s.set_target(mesh="mini_face_LOD2").ok and s.lod_meshes() == [("mini_face_LOD1", 1)] and s.set_target(mesh="mini_face").ok)
    s.add_lod_mesh("mini_face_LOD2", 2)
    p_rt = tmp / "src" / "rt.fcpose.json"
    fcpose_io.save(s.doc, p_rt)
    check("保存の往復: lodMeshes が残る", [(m.mesh, m.lod) for m in fcpose_io.load(p_rt).target.lod_meshes] == [("mini_face_LOD1", 1), ("mini_face_LOD2", 2)])

    # ============================================================ 2. 検証（ベイク前）
    iss = s.validate()
    codes = [i.code for i in iss]
    no_t = [i for i in iss if i.code == "lod_mesh_no_targets"]
    check("検証: 対応するターゲットが全部ある LOD1 は警告なし・LOD2（2 本だけ）は「4 個のうち 2 個」の警告", len(no_t) == 1 and no_t[0].name == "mini_face_LOD2" and "4 個のうち 2 個" in no_t[0].message and set(no_t[0].candidates) == {"smile_R", "brow_up"}, str([(i.name, i.message) for i in no_t]))
    check("検証: ベイク前は LOD の補正が足りないとは言わない（顔にもまだ無い）", "lod_mesh_unbaked" not in codes)

    # ============================================================ 3. ベイク
    rep = s.bake_all()
    nodes = {m: scene.primary_blend_shape(m) for m in (face, lod1, lod2)}
    check("ベイク: LOD1 は自分の blendShape ノード（bs_lod1）に焼く・LOD2 は既にある bs_lod2", nodes[lod1] == "bs_lod1" and nodes[lod2] == "bs_lod2", str(nodes))
    fc_face = {a for a in scene.target_indices(nodes[face]) if naming.is_fc_name(a)}
    fc1 = {a for a in scene.target_indices(nodes[lod1]) if naming.is_fc_name(a)}
    fc2 = {a for a in scene.target_indices(nodes[lod2]) if naming.is_fc_name(a)}
    check("ベイク: 顔と同じ FC_ 名が LOD1 / LOD2 にもできる（Ex も）", fc_face and fc1 == fc_face and fc2 == fc_face and any(a.endswith("_Ex") for a in fc1), f"{len(fc_face)} {len(fc1)} {len(fc2)}")
    names = {"face": face, "lod1": lod1, "lod2": lod2}
    dmax = {k: 0.0 for k in names}
    for li, layer in enumerate(s.doc.layers):
        for (r, c), pt in layer.points.items():
            if pt.pose.is_empty():
                continue
            for k, mesh in names.items():
                want = pose_delta(s.doc, V.clamp_extreme(pt.pose), mesh)
                if li > 0:
                    npt = s.doc.layers[0].points.get((r, c))
                    if npt is not None and not npt.pose.is_empty():
                        want = want - pose_delta(s.doc, V.clamp_extreme(npt.pose), mesh)
                got = dense(nodes[mesh], naming.morph_name(asset, layer.name, r, c), mesh)
                dmax[k] = max(dmax[k], err(got, want))
    check("ベイク: LOD1 の差分 = そのメッシュにポーズを当てた差分（感情は Neutral を引く）・顔も従来どおり（1e-5）", dmax["lod1"] < 2e-5 and dmax["face"] < 2e-5 and dmax["lod2"] < 2e-5, str(dmax))
    n12 = dense(nodes[lod1], naming.morph_name(asset, "Neutral", 1, 2), lod1)
    check("ベイク: LOD1 の差分は動いている（0 でない）・頂点数は LOD1 の数", float(np.abs(n12).max()) > 0.05 and n12.shape[0] == n1)
    # 誇張: FC + Ex = 作った通りのポーズ（LOD1 でも）
    ex12 = dense(nodes[lod1], naming.morph_name(asset, "Neutral", 1, 2, extreme=True), lod1)
    full12 = pose_delta(s.doc, s.doc.layers[0].points[(1, 2)].pose, lod1)
    check("ベイク: LOD1 でも FC + Ex = 作った通りのポーズ（重み 1.5）", float(np.abs(ex12).max()) > 0.01 and err(n12 + ex12, full12) < 2e-5, f"{err(n12 + ex12, full12):.2e}")
    # LOD2: 対応するターゲットが 2 本だけ → smile_R / brow_up のぶんは動かない
    pose_sr = SourcePose({"bs.smile_R": 1.0}, {})
    check("ベイク: LOD2 は対応するターゲット（smile_R）が無いポーズでは動かない（期待値も 0）", float(np.abs(pose_delta(s.doc, pose_sr, lod2)).max()) < 1e-9 and float(np.abs(pose_delta(s.doc, pose_sr, face)).max()) > 0.05)
    # 報告
    check("報告: メッシュごとの数（顔・LOD1・LOD2）", set(rep.per_mesh) == {"mini_face", "mini_face_LOD1", "mini_face_LOD2"} and rep.per_mesh["mini_face_LOD1"]["targets"] == len(fc_face) and rep.per_mesh["mini_face"]["vertices"] > rep.per_mesh["mini_face_LOD1"]["vertices"] > 0, str(rep.per_mesh))
    check("報告: 全体の頂点数 = メッシュ別の合計・メッシュ別の行が画面の報告に出る", rep.total_vertices == sum(c["vertices"] for c in rep.per_mesh.values()) and "メッシュ別  mini_face_LOD1" in ui_grid_report(rep), ui_grid_report(rep)[-300:])
    check("報告: 作成数は顔のぶんだけ数える（メッシュの数で増えない）", len(rep.created) == len(fc_face), f"{len(rep.created)} vs {len(fc_face)}")
    # 状態・記録
    st1 = scene.get_bake_state(nodes[lod1])
    check("状態: LOD1 の blendShape にも bake state と除外・強さの指紋が記録される（顔と同じ）", st1 == scene.get_bake_state(nodes[face]) and scene.get_bake_exclude(nodes[lod1]) == scene.get_bake_exclude(nodes[face]) and len(st1) == len(fc_face))
    check("状態: 焼いた直後は変更ありなし・LOD の補正不足の警告も出ない", s.stale_points() == [] and not [i for i in s.validate() if i.code in ("lod_mesh_unbaked", "point_changed_since_bake", "orphan_target")], str([(i.code, i.message) for i in s.validate()][:3]))
    check("ベイク: 元の姿勢に戻っている（LOD1 の重み・ジョイント）", all(abs(cmds.getAttr(f"bs_lod1.{a}")) < 1e-9 for a in scene.target_indices("bs_lod1")))

    # ============================================================ 4. 部位別の強さ・点だけベイク・孤立の掃除
    s.add_part_strength("smile", 0.5)
    check("強さ: 足すと全点が変更あり（LOD を足しても同じ判定）", len(s.stale_points()) == len({(li, r, c) for li, _l, (r, c), _p in V._bake_candidates(s.doc)}))
    rep_p = s.bake_stale()
    sp_doc = s.doc
    got = dense(nodes[lod1], naming.morph_name(asset, "Neutral", 1, 2), lod1)
    want = pose_delta(s.doc, SourcePose({"bs.mouth_open": 1.0, "bs.smile_L": 0.4}, {}), lod1)
    check("強さ: LOD1 にも掛かる（smile_L 0.4 のポーズの差分と一致）", err(got, want) < 2e-5, f"{err(got, want):.2e}")
    check("強さ: 焼き直すと LOD の記録も新しい指紋・変更なし", s.stale_points() == [] and set(scene.get_bake_exclude(nodes[lod1]).values()) == {V.exclude_signature(s.doc)}, str(set(scene.get_bake_exclude(nodes[lod1]).values())))
    s.remove_part_strength(0)
    s.bake_all()
    # 1 点だけ焼く
    before_other = dense(nodes[lod1], naming.morph_name(asset, "Neutral", 0, 2), lod1)
    s.doc.layers[0].points[(1, 0)].pose.curves = {"bs.smile_R": 0.3}
    s.refresh_scene(notify=False)
    check("点: ポーズを変えた点は変更あり", (0, 1, 0) in s.stale_points(), str(s.stale_points()))
    s.bake_point(1, 0, 0)
    got10 = dense(nodes[lod1], naming.morph_name(asset, "Neutral", 1, 0), lod1)
    check("点: その点だけ焼くと LOD1 の差分も新しいポーズ・ほかの点は触らない", err(got10, pose_delta(s.doc, SourcePose({"bs.smile_R": 0.3}, {}), lod1)) < 2e-5 and err(dense(nodes[lod1], naming.morph_name(asset, "Neutral", 0, 2), lod1), before_other) < 1e-9)
    # 孤立: 点を消して焼くと LOD の FC_ も消える
    s.doc.layers[1].points.pop((0, 2), None)
    s.refresh_scene(notify=False)
    orphan = naming.morph_name(asset, "Joy", 0, 2)
    check("孤立: 点を消すと検証が孤立の FC_ を知らせる（顔・LOD1・LOD2 に残っている）", orphan in scene.target_indices(nodes[lod1]) and any(i.code == "orphan_target" and i.name == orphan for i in s.validate()))
    rep_o = s.bake_all()
    check("孤立: 焼くと顔・LOD1・LOD2 のどれからも消え、記録も消える", all(orphan not in scene.target_indices(nodes[m]) for m in (face, lod1, lod2)) and all(orphan not in scene.get_bake_state(nodes[m]) and orphan not in scene.get_bake_exclude(nodes[m]) for m in (face, lod1, lod2)) and orphan in rep_o.removed, str(rep_o.removed))
    # LOD に FC_ が足りない（あとから LOD を足した）→ 警告
    scene.delete_targets(nodes[lod1], [naming.morph_name(asset, "Neutral", 2, 1)])
    s.refresh_scene(notify=False)
    miss = [i for i in s.validate() if i.code == "lod_mesh_unbaked"]
    check("検証: LOD のメッシュに FC_ が足りないと警告（ベイクで作られる）", len(miss) == 1 and miss[0].name == "mini_face_LOD1" and "1 本足りません" in miss[0].message, str([(i.name, i.message) for i in miss]))
    s.bake_all()
    check("検証: 焼き直すと警告が消える", not [i for i in s.validate() if i.code == "lod_mesh_unbaked"])

    # ============================================================ 5. blendShape の無い LOD（LOD3）と、メッシュが無いとき
    s.add_lod_mesh("mini_face_LOD3")
    s.bake_all()
    n3node = scene.primary_blend_shape(lod3)
    check("LOD3（blendShape なし）: tdFacial_<mesh> をスキンより前に作り、同じ FC_ 名がある（差分は空）", n3node == "tdFacial_mini_face_LOD3" and scene.blend_shapes(lod3) and scene._blend_shape_before_skin(lod3) == [n3node] and {a for a in scene.target_indices(n3node) if naming.is_fc_name(a)} == {a for a in scene.target_indices(nodes[face]) if naming.is_fc_name(a)}, str(n3node))
    check("LOD3: 動かすターゲットが無いので差分はどれも空", all(float(np.abs(dense(n3node, a, lod3)).max()) < 1e-12 for a in scene.target_indices(n3node)))
    check("検証: LOD3 は 4 個のうち 4 個の警告", any(i.code == "lod_mesh_no_targets" and i.name == "mini_face_LOD3" and "4 個のうち 4 個" in i.message for i in s.validate()))
    s.remove_lod_mesh("mini_face_LOD3")
    # メッシュが無い
    s.doc.target.lod_meshes.append(__import__("tdrive_facial.core.model", fromlist=["LodMesh"]).LodMesh("ghost_mesh", 4))
    s.refresh_scene(notify=False)
    gi = [i for i in s.validate() if i.code == "lod_mesh_missing"]
    check("検証: シーンにない LOD のメッシュはエラー", len(gi) == 1 and gi[0].severity == V.SEVERITY_ERROR and gi[0].name == "ghost_mesh")
    try:
        s.bake_all()
        ok_raise = False
        msg = ""
    except Exception as exc:  # noqa: BLE001
        ok_raise = True
        msg = str(exc)
    check("ベイク: シーンにない LOD のメッシュがあると止まり、メッセージにメッシュ名（何も壊さない）", ok_raise and "ghost_mesh" in msg and not [a for a in scene.target_indices(nodes[face]) if a.startswith("fcs_")], msg)
    s.select_point(0, 2)
    check("編集: LOD のメッシュが無くても編集はできる（飛ばす）", s.editing)
    s.end_edit(quiet=True)
    check("外す: シーンにない LOD のメッシュも外せる", s.remove_lod_mesh("ghost_mesh").ok and all(m != "ghost_mesh" for m, _ in s.lod_meshes()))

    # ============================================================ 6. パース補正のキー
    s.set_perspective_enabled(True)
    pa = SourcePose({"bs.mouth_open": 0.5, "bs.smile_L": 0.8}, {"eye_R": BoneOffset(t=(0.2, 0.0, 0.0))})
    s.add_perspective_key(30.0, pa)
    s.bake_all()
    k0 = "FC_mini_Persp_K0"
    check("パース補正: LOD1 にも Persp のシェイプができ、差分 = そのメッシュへのポーズの差分", k0 in scene.target_indices(nodes[lod1]) and err(dense(nodes[lod1], k0, lod1), pose_delta(s.doc, pa, lod1)) < 2e-5 and float(np.abs(dense(nodes[lod1], k0, lod1)).max()) > 0.02)
    check("パース補正: 焼いた直後は変更なし", s.stale_perspective_keys() == [])

    # ============================================================ 7. プレビュー
    s.set_perspective_enabled(False)
    s.bake_all()
    s.set_perspective_enabled(True)
    rep_pv = pr.build_ex(s.doc, cam1)
    rig = rep_pv.rig
    tmap = pr._read_json(rig, pr.TARGETS_ATTR, {})
    sample = next(a for a in tmap if naming.is_fc_name(a) and "_Ex" not in a and "_Persp_" not in a)
    plug_nodes = {p.split(".")[0] for p in tmap[sample]}
    check("プレビュー: FC_ のターゲットに 顔・LOD1・LOD2 の plug が配線される", plug_nodes == {nodes[face], nodes[lod1], nodes[lod2]}, str(plug_nodes))
    cmds.setAttr(cam1 + ".translate", head_c[0] + 30, head_c[1] + 10, head_c[2] + 45)
    worst = 0.0
    nonzero = 0
    for alias, plugs in tmap.items():
        vals = [float(cmds.getAttr(p)) for p in plugs]
        worst = max(worst, max(vals) - min(vals))
        nonzero += any(abs(v) > 1e-6 for v in vals)
    check("プレビュー: 同じ名前の FC_ は顔・LOD1・LOD2 で同じ重み（1e-6）・動いているものがある", worst < 1e-6 and nonzero > 0, f"worst={worst:.2e} nonzero={nonzero}")
    check("プレビュー: 作り直しの必要なし（is_stale = False）", not pr.is_stale(s.doc))
    s.remove_lod_mesh("mini_face_LOD2")
    after = {pl.split(".")[0] for pl in pr._read_json(rig, pr.TARGETS_ATTR, {}).get(sample, [])}
    check("プレビュー: LOD を外すと rig は古くなる（セッションが自動で作り直し、LOD2 の配線が外れる）", pr.is_stale(s.doc) or nodes[lod2] not in after, str(after))
    s.undo()
    again = {pl.split(".")[0] for pl in pr._read_json(rig, pr.TARGETS_ATTR, {}).get(sample, [])}
    check("プレビュー: 元に戻すと LOD2 も配線される", pr.is_stale(s.doc) or nodes[lod2] in again, str(again))
    pr.delete(asset)

    # ============================================================ 8. 出力（FBX）
    rep_pv2 = pr.build_ex(s.doc, cam1)
    res = export.export_unity(s.doc, None, out_dir=tmp / "unity_out")
    fcn = len([a for a in scene.target_indices(nodes[lod1]) if naming.is_fc_name(a)])
    check("出力: メッシュに LOD1・LOD2 が入る（顔・LOD1・LOD2 + 同じ骨格の mini_brow）", {"mini_face", "mini_face_LOD1", "mini_face_LOD2"} <= set(res["meshes"]), str(res["meshes"]))
    check("出力: 結果の LOD の表（LOD 番号）・メッシュ別の FC_ の数", res["lod_meshes"] == {"mini_face_LOD1": 1, "mini_face_LOD2": 2} and res["fc_by_mesh"].get("mini_face_LOD1") == fcn and res["fc_count"] == fcn, str((res["lod_meshes"], res["fc_by_mesh"], res["fc_count"])))
    from pathlib import Path as _P

    class _R:  # 結果の文章（画面と同じ）
        pass

    r_obj = export.UnityExportResult(fbx=_P(res["fbx"]), fcpose=_P(res["fcpose"]), meshes=res["meshes"], blendshapes=res["blendshapes"], fc_by_mesh=res["fc_by_mesh"], lod_meshes=res["lod_meshes"], fc_count=res["fc_count"], excluded_fcs=res["excluded_fcs"])
    txt = ui_export.ExportTab.result_text(r_obj)
    check("出力: 結果の文章に LOD のメッシュの行（LOD1 mini_face_LOD1・FC_* の本数）", "LOD のメッシュ:" in txt and f"LOD1 mini_face_LOD1（FC_* {fcn} 本）" in txt and "LOD2 mini_face_LOD2" in txt, txt)
    check("出力: .fcpose に lodMeshes が入る", [(m.mesh, m.lod) for m in fcpose_io.load_document(res["fcpose"]).target.lod_meshes] == [("mini_face_LOD1", 1), ("mini_face_LOD2", 2)])
    check("出力: 警告なし（全部ベイク済み）", not res["warnings"], str(res["warnings"]))
    pr.delete(asset)
    # FBX を新しいシーンへ取り込んで確かめる
    cmds.file(rename=str(tmp / "orig.mb"))
    cmds.file(save=True, type="mayaBinary")
    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval("FBXResetImport")
    mel.eval("FBXImportMode -v add")
    mel.eval(f'FBXImport -f "{Path(res["fbx"]).as_posix()}"')
    xf = {m.split("|")[-1]: m for m in cmds.ls(type="transform", long=True)}
    check("FBX 取り込み: mini_face_LOD1 / mini_face_LOD2 が入っている", {"mini_face", "mini_face_LOD1", "mini_face_LOD2"} <= set(xf), str(sorted(xf)[:12]))
    fbx_fc = {}
    for nm in ("mini_face", "mini_face_LOD1", "mini_face_LOD2"):
        al = [a for n in scene.blend_shapes(xf[nm]) for a in scene.target_indices(n)]
        fbx_fc[nm] = {a for a in al if a.startswith("FC_")}
    check("FBX 取り込み: LOD1 / LOD2 にも顔と同じ FC_ のシェイプがある・fcs_ は無い", fbx_fc["mini_face_LOD1"] == fbx_fc["mini_face"] == fbx_fc["mini_face_LOD2"] and len(fbx_fc["mini_face"]) == fcn, str({k: len(v) for k, v in fbx_fc.items()}))
    check("FBX 取り込み: LOD1 の頂点数が違う（72）まま・元のシェイプ（mouth_open など）も入っている", scene.vertex_count(xf["mini_face_LOD1"]) == n1 and {"mouth_open", "smile_L"} <= {a for n in scene.blend_shapes(xf["mini_face_LOD1"]) for a in scene.target_indices(n)})
    cmds.file(new=True, force=True)

    # ============================================================ 9. 画面（セットアップタブ）
    facial_fixture.build_mini_head()
    facial_fixture.add_lod1("mini_face_LOD1", "bs_lod1")
    facial_fixture.add_lod1("mini_face_LOD2", "bs_lod2", divisions=(6, 4), aliases=("mouth_open", "smile_L"))
    s.close()
    s.open(p_doc)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    panel = ui.FacialPanel()
    panel.resize(480, 900)
    pump()
    setup = panel.tab("setup")
    setup.refresh()
    tbl = setup.lod_table
    check("画面: 「LOD のメッシュ」の表（メッシュ・LOD）と「選択から追加」「外す」がある・最初は空", tbl.columnCount() == 2 and tbl.rowCount() == 0 and setup.lod_add.text() == "選択から追加" and setup.lod_remove.text() == "外す" and not setup.lod_remove.isEnabled())
    cmds.select("mini_face_LOD1")
    setup.on_lod_add()
    cmds.select("mini_face_LOD2")
    setup.on_lod_add()
    setup.refresh()
    check("画面: 選択から追加すると次の LOD 番号（1, 2）で表に出る", tbl.rowCount() == 2 and tbl.item(0, 0).text() == "mini_face_LOD1" and tbl.cellWidget(0, 1).value() == 1 and tbl.cellWidget(1, 1).value() == 2 and s.lod_meshes() == [("mini_face_LOD1", 1), ("mini_face_LOD2", 2)], setup.status.text())
    tbl.cellWidget(1, 1).setValue(3)
    check("画面: 番号のスピンボックスを変えると文書に入る（表は作り直さない）", s.lod_meshes()[1] == ("mini_face_LOD2", 3) and tbl.rowCount() == 2 and tbl.cellWidget(1, 1).value() == 3, str(s.lod_meshes()))
    cmds.select("mini_face")
    setup.on_lod_add()
    check("画面: 顔のメッシュを足そうとするとエラーの表示（一覧は変わらない）", "既に" in setup.status.text() and len(s.lod_meshes()) == 2, setup.status.text())
    cmds.select(clear=True)
    setup.on_lod_add()
    check("画面: 何も選ばずに押すと案内", "選んでから" in setup.status.text())
    tbl.selectRow(0)
    setup.on_lod_remove()
    check("画面: 選んで「外す」と消える", s.lod_meshes() == [("mini_face_LOD2", 3)] and tbl.rowCount() == 1 and tbl.item(0, 0).text() == "mini_face_LOD2")
    tbl.clearSelection()
    setup.on_lod_remove()
    check("画面: 選ばずに「外す」は案内を出して何も消さない", "選んでください" in setup.status.text() and len(s.lod_meshes()) == 1)
    s.undo()
    s.undo()
    setup.refresh()
    check("画面: 元に戻すで表も戻る", tbl.rowCount() == 2 and tbl.cellWidget(1, 1).value() == 2, str(s.lod_meshes()))
    hint = " ".join(l.text() for l in setup.findChildren(QtWidgets.QLabel))
    check("画面: 説明（同じ名前のシェイプを焼く・無いターゲットは動かない・ゲームでは全 LOD に同じ重み）", all(t in hint for t in ("頂点数の違う", "同じ名前のシェイプとして焼きます", "無いターゲットは、そのメッシュでは動きません", "すべての LOD のメッシュに書かれます")), hint[-500:])
    check("画面: 文言にチケット名・準備中が無い", not re.search(r"F\d|準備中|チケット", hint))

    # 部位別の強さを入れて、2 つの箱を 1 枚の画像にする
    s.add_part_strength("eye", 0.5)
    s.add_part_strength("smile", 0.8)
    s.add_exclude("curve", "brow")
    setup.refresh()
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)
        panel.select_tab("setup")
        panel.resize(480, 1700)
        pump()
        boxes = {g.title(): g for g in setup.findChildren(QtWidgets.QGroupBox)}
        pix_lod = boxes["対象のメッシュ"].grab()
        pix_ps = boxes["部位別の強さ"].grab()
        w = max(pix_lod.width(), pix_ps.width())
        canvas = QtGui.QPixmap(w, pix_lod.height() + pix_ps.height() + 12)
        canvas.fill(QtGui.QColor("#444444"))
        painter = QtGui.QPainter(canvas)
        painter.drawPixmap(0, 0, pix_lod)
        painter.drawPixmap(0, pix_lod.height() + 12, pix_ps)
        painter.end()
        canvas.save(str(Path(shot_dir) / "setup_parts_lod.png"))
        pix_lod.save(str(Path(shot_dir) / "setup_lod.png"))
        pix_ps.save(str(Path(shot_dir) / "setup_parts.png"))
    panel.detach()
    s.close()
    shutil.rmtree(tmp, ignore_errors=True)


def ui_grid_report(rep) -> str:
    from tdrive_facial import ui_grid

    return ui_grid.GridTab.report_text(rep)


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
