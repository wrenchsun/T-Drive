"""FacialController の Maya 側（scene / pose_apply / bake）のスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_smoke.py

合成の小さな頭（tests/maya/facial_fixture.py）で確かめる。shizuku（assets/shizuku/shizuku.mb）があれば第 2 部で結合テストを行う
（`TDRIVE_FACIAL_SHIZUKU=0` で飛ばす。シーンは開くだけで保存しない）。
"""

from __future__ import annotations

import copy
import hashlib
import os
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


# ---------------------------------------------------------------- 共通の道具


def joint_state(joints) -> dict:
    from maya import cmds

    return {
        j: tuple(tuple(round(v, 9) for v in cmds.getAttr(f"{j}.{a}")[0]) for a in ("translate", "rotate", "scale", "jointOrient"))
        for j in joints
    }


def weight_state(node: str) -> dict:
    from maya import cmds

    idx = cmds.getAttr(node + ".weight", multiIndices=True) or []
    return {i: round(cmds.getAttr(f"{node}.weight[{i}]"), 9) for i in idx}


def targets_digest(node: str, only_non_fc: bool = False) -> list:
    """ターゲットごとの（番号, エイリアス, 頂点, 差分）。差分は 1e-9 に丸める。"""
    import numpy as np

    from tdrive_facial import scene
    from tdrive_facial.core import naming

    out = []
    for alias, idx in sorted(scene.target_indices(node).items(), key=lambda kv: kv[1]):
        if only_non_fc and naming.is_fc_name(alias):
            continue
        comps, d = scene.read_target_delta(node, alias)
        out.append((idx, alias, tuple(comps), hashlib.sha1(np.round(d, 9).tobytes()).hexdigest()))
    return out


def node_state(node: str) -> tuple:
    from maya import cmds

    from tdrive_facial import scene

    return (
        tuple(cmds.aliasAttr(node, query=True) or []),
        tuple(cmds.getAttr(node + ".weight", multiIndices=True) or []),
        tuple(scene._group_indices(node)),  # Undo の後に空の group の要素だけ残ることがあるので、中身のあるものだけ比べる
        tuple(targets_digest(node)),
        tuple(sorted(scene.get_bake_state(node).items())),
    )


def quat_close(a, b, tol=1e-4) -> bool:
    d = abs(sum(x * y for x, y in zip(a, b)))
    return abs(d - 1.0) < tol * tol or max(abs(x - y) for x, y in zip(a, b)) < tol or max(abs(x + y) for x, y in zip(a, b)) < tol


def codes(issues) -> list[str]:
    return [i.code for i in issues]


def max_diff(a, b) -> float:
    import numpy as np

    return float(np.abs(np.asarray(a) - np.asarray(b)).max())


# ---------------------------------------------------------------- 第 1 部: 合成の頭


def run_mini() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive_facial import bake as bakemod
    from tdrive_facial import pose_apply, scene
    from tdrive_facial.core import naming, validate
    from tdrive_facial.core.model import BoneOffset, SourcePose

    names = facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    doc = facial_fixture.make_doc()
    joints = scene.mesh_joints([face])

    # ---- 名前の対応・メッシュ・基準ボーン
    cs = scene.list_curves(face)
    expected = {"bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"}
    check("シェイプ名: 一覧が bs.<ターゲット> の 4 本", {c.name for c in cs} == expected, str(scene.curve_names(face)))
    rt = all(scene.resolve_curve(c.name, face) == c and scene.parse_curve_name(c.name) == (c.node, c.alias) for c in cs)
    check("シェイプ名: 名前 → (ノード, 番号, エイリアス) の往復", rt)
    check("シェイプ名: ノード名なしは顔メッシュの最初の blendShape で解決", scene.resolve_curve("smile_L", face) == scene.resolve_curve("bs.smile_L", face))
    check("シェイプ名: 無い名前・無いノードは None", scene.resolve_curve("bs.nothing", face) is None and scene.resolve_curve("zz.smile_L", face) is None)
    check("基準ボーンの自動検出（シーン）", scene.detect_base_bone_for([face]) == "head", scene.detect_base_bone_for([face]))
    check(
        "基準ボーンの自動検出（名前の規則）",
        scene.detect_base_bone(["Hips", "Spine", "mixamorig:Head", "J_Bip_C_Head"]) == "J_Bip_C_Head"
        and scene.detect_base_bone(["root", "forehead", "bone_head"]) == "bone_head"
        and scene.detect_base_bone(["root", "Head1", "x"]) == "Head1"
        and scene.detect_base_bone(["root", "spine"]) == "",
    )
    vis = scene.list_visible_meshes()
    check(
        "非表示のターゲットメッシュは一覧に出ない",
        {scene.short_name(v) for v in vis} == {"mini_face", "mini_brow"} and names["hidden_target"] not in [scene.short_name(v) for v in vis],
        str(vis),
    )
    check("blendShape: 顔は bs、スキンより前", scene.primary_blend_shape(face) == "bs" and scene.blend_shapes(face) == ["bs"])

    # ---- ポーズの当て / 取り込み
    with scene.enter_reference_pose([face], extra_joints=["eye_L", "eye_R"]) as ref:
        check("基準姿勢: バインドポーズあり・警告なし", ref.has_bind_pose and not ref.warnings, str(ref.warnings))
        ok_all, worst = True, 0.0
        for (r, c), pt in sorted(doc.layers[0].points.items()):
            rep = pose_apply.apply_pose(doc, pt.pose, ref)
            got = pose_apply.capture_pose(doc, ref)
            same_c = set(got.curves) == set(pt.pose.curves) and all(abs(got.curves[k] - v) < 1e-4 for k, v in pt.pose.curves.items())
            same_b = set(got.bones) == set(pt.pose.bones)
            for k, b in pt.pose.bones.items():
                if k in got.bones:
                    g = got.bones[k]
                    err = max(max(abs(x - y) for x, y in zip(g.t, b.t)), max(abs(x - y) for x, y in zip(g.s, b.s)))
                    worst = max(worst, err)
                    same_b &= err < 1e-4 and quat_close(g.r, b.r)
            ok_all &= same_c and same_b and rep.ok
        check("ポーズ: 当てる → 取り込むの往復（シェイプ・ボーン 1e-4 以内、4 点）", ok_all, f"worst={worst}")
        # ボーンの意味: 位置 = 基準 + t、向き = r · 基準
        p = SourcePose({}, {"eye_L": BoneOffset(t=(0.1, 0.2, 0.3), r=facial_fixture._q_axis((0, 1, 0), 30.0), s=(1.0, 1.0, 1.0))})
        pose_apply.apply_pose(doc, p, ref)
        t, q, s = scene.read_local(ref.bones["eye_L"].path)
        base = ref.bones["eye_L"]
        exp_q = scene.quat_mul(p.bones["eye_L"].r, base.q)
        check(
            "ボーンの意味: 位置は加算・向きは offset · base（親の空間）",
            max(abs(a - (b + o)) for a, b, o in zip(t, base.t, (0.1, 0.2, 0.3))) < 1e-5 and quat_close(q, exp_q, 1e-5),
            f"{q} vs {exp_q}",
        )
        rep = pose_apply.apply_pose(doc, SourcePose({"bs.smile_L": 0.5, "bs.nothing": 1.0, "xx.mouth_open": 1.0}, {"no_bone": BoneOffset(t=(1, 0, 0))}), ref)
        check(
            "フェイルソフト: 無い名前は飛ばして報告（例外なし）",
            rep.missing_curves == ["bs.nothing", "xx.mouth_open"] and rep.missing_bones == ["no_bone"] and abs(cmds.getAttr("bs.smile_L") - 0.5) < 1e-9,
            str(rep),
        )
        d2 = copy.deepcopy(doc)
        d2.exclude.curves = ["bs.smile_L"]
        d2.exclude.bones = ["eye_L"]
        pose_apply.apply_pose(d2, SourcePose({"bs.smile_L": 1.0, "bs.mouth_open": 1.0}, {"eye_L": BoneOffset(t=(5, 0, 0))}), ref)
        cap = pose_apply.capture_pose(d2, ref)
        check("除外: doc.exclude のシェイプ・ボーンは触らない / 取り込まない", cmds.getAttr("bs.smile_L") == 0 and cmds.getAttr("bs.mouth_open") == 1 and "eye_L" not in cap.bones)
        d3 = copy.deepcopy(doc)
        d3.working_set.curves = ["bs.mouth_open"]
        pose_apply.apply_pose(d3, SourcePose({"bs.smile_L": 1.0, "bs.mouth_open": 1.0}), ref)
        check("取り込み: 作業セットだけ", list(pose_apply.capture_pose(d3, ref, working_set_only=True).curves) == ["bs.mouth_open"] and len(pose_apply.capture_pose(d3, ref).curves) == 2)
        pose_apply.reset_to_reference(ref)
        check("reset_to_reference: 重み 0・ジョイントが基準", joint_state(joints) == {j: tuple(tuple(round(v, 9) for v in ref.bones[scene.short_name(j)].raw[a]) for a in ("translate", "rotate", "scale", "jointOrient")) for j in joints} and not any(weight_state("bs").values()))
        try:
            m = copy.deepcopy(doc)
            m.meta.up_axis = "Z"
            pose_apply.apply_pose(m, SourcePose({}), ref)
            raised = False
        except ValueError as e:
            raised = "Maya" in str(e)
        check("座標系が Maya でなければ明確なエラー", raised)

    # ---- 基準姿勢への出入り（元の姿勢・重み・接続へ戻る）
    cmds.setAttr("eye_L.rotateX", 25)
    cmds.setAttr("head.translateY", cmds.getAttr("head.translateY") + 0.7)
    cmds.setAttr("bs.smile_L", 0.4)
    curve = cmds.createNode("animCurveTU", name="smileR_anim")
    cmds.setKeyframe(curve, time=0, value=0.25)
    cmds.connectAttr(curve + ".output", "bs.smile_R")
    before_j, before_w = joint_state(joints), weight_state("bs")
    with scene.enter_reference_pose([face]) as ref:
        in_ref = (
            abs(cmds.getAttr("eye_L.rotateX")) < 1e-6
            and not any(weight_state("bs").values())
            and not cmds.listConnections("bs.smile_R", source=True, destination=False)
        )
    check("基準姿勢: 入ると bind・重み 0・接続が外れ、出ると姿勢・重み・接続が元に戻る",
          in_ref and joint_state(joints) == before_j and weight_state("bs") == before_w
          and cmds.listConnections("bs.smile_R", source=True, destination=False) == [curve])
    try:
        with scene.enter_reference_pose([face]) as ref:
            raise KeyError("途中で失敗")
    except KeyError:
        pass
    check("基準姿勢: 例外でも元へ戻る", joint_state(joints) == before_j and weight_state("bs") == before_w)
    cmds.disconnectAttr(curve + ".output", "bs.smile_R")
    cmds.delete(curve)
    cmds.setAttr("bs.smile_L", 0.0)
    cmds.setAttr("bs.smile_R", 0.0)

    # ---- ベイク（元の姿勢を乱した状態から）
    cmds.setAttr("eye_R.rotateY", -12)
    cmds.setAttr("bs.mouth_open", 0.3)
    pre_j, pre_w = joint_state(joints), weight_state("bs")
    pre_non_fc = targets_digest("bs", only_non_fc=True)
    pre_alias = tuple(cmds.aliasAttr("bs", query=True) or [])
    rep = bakemod.bake(doc)
    expect_names = [
        naming.morph_name("mini", "Neutral", 1, 2),
        naming.morph_name("mini", "Neutral", 1, 0),
        naming.morph_name("mini", "Neutral", 0, 2),
        naming.morph_name("mini", "Neutral", 2, 1),
        naming.morph_name("mini", "Joy", 1, 2),
        naming.morph_name("mini", "Joy", 0, 2),
    ]
    check("ベイク: 6 点のターゲットができる", sorted(rep.created) == sorted(expect_names) and not rep.replaced and not rep.removed, rep.summary())
    check("ベイク: 要約（頂点数・時間）", rep.total_vertices > 0 and rep.seconds > 0 and all(rep.vertex_counts[n] > 0 for n in expect_names), str(rep.vertex_counts))
    check("ベイク後: ジョイントの姿勢・重みが前と同じ", joint_state(joints) == pre_j and weight_state("bs") == {**pre_w, **{k: 0.0 for k in weight_state("bs") if k not in pre_w}}, "")
    check("既存ターゲットの番号・エイリアス・差分が不変", targets_digest("bs", only_non_fc=True) == pre_non_fc and tuple(cmds.aliasAttr("bs", query=True))[: len(pre_alias)] == pre_alias)
    idx_now = scene.target_indices("bs")
    check("新しいターゲットは末尾（最大 + 1 から）に追加", sorted(idx_now[n] for n in expect_names) == list(range(4, 10)), str(idx_now))
    check("非表示のターゲットメッシュ以外のメッシュが増えていない", len(cmds.ls(type="mesh", noIntermediate=True)) == 3, str(cmds.ls(type="mesh", noIntermediate=True)))

    def posed(pose):
        with scene.enter_reference_pose([face], extra_joints=["eye_L", "eye_R"]) as ref:
            pose_apply.apply_pose(doc, pose, ref)
            return scene.read_points(face)

    def with_targets(weights: dict):
        with scene.enter_reference_pose([face]) as ref:
            for n, w in weights.items():
                cmds.setAttr(f"bs.{n}", w)
            return scene.read_points(face)

    neutral = doc.layers[0]
    worst = {}
    for (r, c), pt in sorted(neutral.points.items()):
        n = naming.morph_name("mini", "Neutral", r, c)
        worst[(r, c)] = max_diff(posed(pt.pose), with_targets({n: 1.0}))
    check("焼いた FC_* = 1（基準姿勢）はポーズと同じ形（シェイプのみの点・1e-3 cm）", worst[(1, 2)] < 1.001e-3 and worst[(1, 0)] < 1.001e-3, str(worst))
    check("焼いた FC_* = 1 はポーズと同じ形（目のボーンを回した点・1e-3 cm）", worst[(0, 2)] < 1.001e-3 and worst[(2, 1)] < 1.001e-3, str(worst))
    pj = doc.layers[1].points[(1, 2)].pose
    jn = naming.morph_name("mini", "Joy", 1, 2)
    nn = naming.morph_name("mini", "Neutral", 1, 2)
    check("差分ベイク: Neutral(1) + Joy(1) = Joy のポーズ", max_diff(posed(pj), with_targets({nn: 1.0, jn: 1.0})) < 2.1e-3, str(max_diff(posed(pj), with_targets({nn: 1.0, jn: 1.0}))))
    pj2 = doc.layers[1].points[(0, 2)].pose
    check(
        "差分ベイク（目のボーンを含む点）: Neutral(1) + Joy(1) = Joy のポーズ",
        max_diff(posed(pj2), with_targets({naming.morph_name("mini", "Neutral", 0, 2): 1.0, naming.morph_name("mini", "Joy", 0, 2): 1.0})) < 2.1e-3,
    )
    check("差分ベイク: Joy だけ 1 にしても Neutral の分は含まない", max_diff(posed(pj), with_targets({jn: 1.0})) > 0.1)

    # ---- ベイクの状態と検証
    state = scene.bake_state_for(face)
    check("ベイクの状態: 全ターゲットの pose_hash が blendShape ノードに入る", state == {n: validate.pose_hash(doc.layers[0 if n.startswith('FC_mini_Neutral') else 1].points[(int(n[-4]), int(n[-1]))].pose) for n in expect_names}, str(state))
    info = scene.build_scene_info(doc)
    issues = validate.validate(doc, info, bake_state=state)
    bad = [i for i in issues if i.code in ("point_unbaked", "point_changed_since_bake", "baked_morph_missing", "orphan_target")]
    check("検証: ベイク後は未ベイク・変更あり・欠けが無い", not bad, str([(i.code, i.name) for i in bad]))
    check(
        "SceneInfo: シェイプ名・骨格・FC_ のターゲット",
        set(info.curves) == expected and {"root", "head", "eye_L", "eye_R"} <= set(info.bones) and info.bone_parents["eye_L"] == "head"
        and set(info.targets) == set(expect_names) and info.target_info[nn].vertex_count == rep.vertex_counts[nn],
        str((info.curves, info.targets)),
    )
    doc2 = copy.deepcopy(doc)
    doc2.layers[0].points[(1, 2)].pose.curves["bs.mouth_open"] = 0.9
    issues2 = validate.validate(doc2, scene.build_scene_info(doc2), bake_state=state)
    check("検証: ポーズを変えると「ベイク後に変更」と出る", [i.name for i in issues2 if i.code == "point_changed_since_bake"] == [nn], str(codes(issues2)))

    # ---- 再ベイク（置き換え・孤立の掃除・他の名前に触らない）
    scene.write_target_delta("bs", "FC_mini_Neutral_R2_C2", __import__("numpy").array([[0.0, 0.5, 0.0]]), [3])  # 孤立
    scene.write_target_delta("bs", "FC_other_Neutral_R0_C0", __import__("numpy").array([[0.0, 0.5, 0.0]]), [3])  # 別アセット
    scene.write_target_delta("bs", "fcs_sculpt_keep", __import__("numpy").array([[0.0, 0.5, 0.0]]), [3])  # 彫り用
    n_idx = len(cmds.getAttr("bs.weight", multiIndices=True))
    old_counts = dict(rep.vertex_counts)
    doc3 = copy.deepcopy(doc)
    doc3.bake.delta_threshold = 0.4
    rep2 = bakemod.bake(doc3)
    ti = scene.target_indices("bs")
    check("再ベイク: 置き換えで番号が増えない（作成 0・置き換え 6）", not rep2.created and sorted(rep2.replaced) == sorted(expect_names) and len(ti) == n_idx - 1, rep2.summary())
    check("再ベイク: 孤立した FC_（この asset）だけ消える。別 asset の FC_・fcs_・既存は残る", rep2.removed == ["FC_mini_Neutral_R2_C2"] and "FC_other_Neutral_R0_C0" in ti and "fcs_sculpt_keep" in ti and "mouth_open" in ti, str(rep2.removed))
    check("しきい値: 大きいと捨てる頂点が出て、残りは全部しきい値以上", rep2.culled_vertices > 0 and rep2.total_vertices < rep.total_vertices and all(
        float(__import__("numpy").linalg.norm(scene.read_target_delta("bs", n)[1], axis=1).min()) >= 0.4 for n in expect_names if rep2.vertex_counts[n]
    ), f"{rep.total_vertices} -> {rep2.total_vertices}, culled {rep2.culled_vertices}")
    check("再ベイク後も既存ターゲットが不変・状態に孤立が残らない", targets_digest("bs", only_non_fc=True)[:4] == pre_non_fc and "FC_mini_Neutral_R2_C2" not in scene.get_bake_state("bs"))
    # 空ポーズの点は孤立になって消える
    doc4 = copy.deepcopy(doc)
    doc4.layers[1].points.pop((0, 2))
    rep3 = bakemod.bake(doc4)
    check("点を消して再ベイク: その FC_ が孤立として消える", rep3.removed == [naming.morph_name("mini", "Joy", 0, 2)] and naming.morph_name("mini", "Joy", 0, 2) not in scene.target_indices("bs"), str(rep3.removed))
    one = bakemod.bake_point(doc, 1, 0, 2)
    check("bake_point: 1 点だけ焼く", one.created == [naming.morph_name("mini", "Joy", 0, 2)] and not one.replaced, one.summary())

    # ---- 削除の制限
    before = node_state("bs")
    refused = 0
    for bad_names in (["mouth_open"], ["FC_mini_Neutral_R1_C2", "mouth_open"], ["bs.mouth_open"]):
        try:
            scene.delete_targets("bs", bad_names)
        except ValueError:
            refused += 1
    check("delete_targets: FC_ / fcs_ 以外は拒否し、何も消えない", refused == 3 and node_state("bs") == before)
    ids_before = scene.target_indices("bs")
    removed = scene.delete_targets("bs", ["fcs_sculpt_keep", "FC_other_Neutral_R0_C0", "FC_not_there"])
    ids_after = scene.target_indices("bs")
    check("delete_targets: FC_/fcs_ は消え、残りの番号は詰まらない", removed == ["fcs_sculpt_keep", "FC_other_Neutral_R0_C0"] and all(ids_after[k] == v for k, v in ids_after.items()) and all(ids_before[k] == ids_after[k] for k in ids_after))

    # ---- ポーズをシェイプに
    shp = bakemod.pose_to_shape(doc, neutral.points[(1, 2)].pose, "fcs_test_shape")
    err = max_diff(posed(neutral.points[(1, 2)].pose), with_targets_any({"fcs_test_shape": 1.0}, face))
    n_after = len(scene.target_indices("bs"))
    shp2 = bakemod.pose_to_shape(doc, neutral.points[(1, 2)].pose, "fcs_test_shape")
    check("pose_to_shape: 新しいターゲット（fcs_）がポーズの形になる", shp.created == ["fcs_test_shape"] and shp.total_vertices > 0 and err < 1.001e-3, f"{err}")
    check("pose_to_shape: 同じ名前は置き換え（番号が増えない）", shp2.replaced == ["fcs_test_shape"] and len(scene.target_indices("bs")) == n_after)
    try:
        bakemod.pose_to_shape(doc, neutral.points[(1, 2)].pose, "mouth_open")
        refused = False
    except bakemod.BakeError:
        refused = True
    check("pose_to_shape: 既存の非 FC ターゲットは上書きしない", refused and targets_digest("bs", only_non_fc=True)[:4] == pre_non_fc)
    check("pose_to_shape: ベイクの状態には入らない", "fcs_test_shape" not in scene.get_bake_state("bs"))

    # ---- 前提が足りないとき
    errs = 0
    for mutate in (lambda d: setattr(d, "asset", None), lambda d: setattr(d.target, "mesh", ""), lambda d: setattr(d.target, "mesh", "no_such_mesh")):
        d = copy.deepcopy(doc)
        mutate(d)
        try:
            bakemod.bake(d)
        except bakemod.BakeError as e:
            errs += 1 if str(e) else 0
    check("前提が足りない（asset / メッシュ未設定・無い）ときは BakeError で止める", errs == 3)

    # ---- 複数メッシュ・blendShape の自動作成・Undo 1 回・元のポーズ復元（新しいシーン）
    facial_fixture.build_mini_head()
    doc = facial_fixture.make_doc()
    doc.target.extra_meshes = ["mini_brow"]
    face = scene.resolve_mesh("mini_face")
    brow = scene.resolve_mesh("mini_brow")
    joints = scene.mesh_joints([face])
    cmds.setAttr("eye_L.rotateZ", 14)
    cmds.setAttr("bs.brow_up", 0.2)
    snap = (node_state("bs"), joint_state(joints), weight_state("bs"))
    cmds.undoInfo(state=True)
    rep = bakemod.bake(doc)
    node_b = scene.primary_blend_shape(brow)
    hist = cmds.listHistory(scene.mesh_shape(brow), pruneDagObjects=True)
    check(
        "extraMeshes: blendShape が無いメッシュには tdFacial_<mesh> がスキンより前にできて、同じ名前の FC_* が入る",
        node_b == "tdFacial_mini_brow" and hist.index(node_b) > hist.index("mini_brow_skin") and set(rep.created) == set(scene.target_indices(node_b)) - set() and len(rep.created) == 6,
        f"{hist} {rep.created}",
    )
    check("ベイク後: 元の姿勢（目 rotateZ 14・brow_up 0.2）に戻っている", joint_state(joints) == snap[1] and abs(weight_state("bs")[3] - 0.2) < 1e-6)
    cmds.undo()
    after = (node_state("bs"), joint_state(joints), weight_state("bs"))
    check("Undo 1 回でベイク全体が戻る（blendShape・状態・作成した tdFacial ノードも）", after == snap and not cmds.objExists("tdFacial_mini_brow"), "")
    cmds.redo()
    check("Redo で再び焼かれた状態になる", naming.morph_name("mini", "Neutral", 1, 2) in scene.target_indices("bs") and cmds.objExists("tdFacial_mini_brow"))

    # 以降の読み取りは Undo の履歴に積まれるので、Undo の確認の後に行う
    n0 = naming.morph_name("mini", "Neutral", 0, 2)

    def brow_posed(pose):
        with scene.enter_reference_pose([face, brow], extra_joints=["eye_L"]) as ref:
            pose_apply.apply_pose(doc, pose, ref)
            return scene.read_points(brow)

    def brow_fc(n):
        with scene.enter_reference_pose([face, brow]) as ref:
            cmds.setAttr(f"{node_b}.{n}", 1.0)
            return scene.read_points(brow)

    ebrow = max_diff(brow_posed(doc.layers[0].points[(0, 2)].pose), brow_fc(n0))
    base_brow = brow_posed(SourcePose())
    moved = max_diff(brow_posed(doc.layers[0].points[(0, 2)].pose), base_brow)
    check("extraMeshes: 別メッシュも目のボーンの回転ぶんが焼かれ、FC_* = 1 でポーズと一致", moved > 0.01 and ebrow < 1.001e-3, f"moved={moved} err={ebrow}")

    # ---- バインドポーズが無いとき
    for dp in cmds.ls(type="dagPose") or []:
        cmds.delete(dp)
    with scene.enter_reference_pose([face]) as ref:
        warned = (not ref.has_bind_pose) and any("バインドポーズ" in w for w in ref.warnings)
    rep = bakemod.bake(doc)
    check("バインドポーズが無い: 今の姿勢を基準にして警告し、ベイクは通る", warned and any("バインドポーズ" in w for w in rep.warnings) and len(rep.created) + len(rep.replaced) == 6, str(rep.warnings))


def with_targets_any(weights: dict, mesh: str):
    from maya import cmds

    from tdrive_facial import scene

    with scene.enter_reference_pose([mesh]) as ref:
        for n, w in weights.items():
            cmds.setAttr(f"bs.{n}", w)
        return scene.read_points(mesh)


# ---------------------------------------------------------------- 第 2 部: shizuku（あるときだけ）


def run_shizuku() -> None:
    from maya import cmds

    from tdrive_facial import bake as bakemod
    from tdrive_facial import pose_apply, scene
    from tdrive_facial.core import autofill, naming, validate
    from tdrive_facial.core.model import BoneOffset, Document, GridPoint, Meta, SourcePose, Target

    mb = REPO / "assets" / "shizuku" / "shizuku.mb"
    cmds.file(mb.as_posix(), open=True, force=True)  # 開くだけ。保存しない
    t0 = time.perf_counter()
    vis = scene.list_visible_meshes()
    hidden = len(cmds.ls(type="mesh", noIntermediate=True)) - len(vis)
    check("shizuku: 表示中のメッシュだけを一覧する（非表示のターゲットメッシュを無視）", 5 <= len(vis) <= 12 and hidden > 50, f"visible={len(vis)} hidden={hidden}")
    face = scene.resolve_mesh("mdl_face02")
    base = scene.detect_base_bone_for([face])
    check("shizuku: 基準ボーンの自動検出 = bone_head", base == "bone_head", base)
    curves = scene.curve_names(face)
    check("shizuku: bs のシェイプ名が bs.<名前> で引ける", "bs.mouth_left" in curves and "bs.eye_close_L" in curves and scene.resolve_curve("bs.eye_close_L", face) is not None, f"{len(curves)} curves")

    doc = Document(meta=Meta(unit="cm", up_axis="Y", handedness="right", source="smoke"))
    doc.asset = "shizuku"
    doc.target = Target(mesh="mdl_face02")
    doc.grid.cols, doc.grid.rows = 5, 3
    doc.grid.base_bone = base
    doc.grid.forward_axis = "+Z"
    doc.mirror.bone_axis = "X"
    q = (0.0, 0.0, 0.0, 1.0)
    doc.layers[0].points[(1, 2)] = GridPoint(1, 2, True, SourcePose({"bs.mouth_left": 0.4}, {"bone_eye_L": BoneOffset(r=(0.0, 0.0871557, 0.0, 0.9961947))}))
    doc.layers[0].points[(1, 4)] = GridPoint(1, 4, True, SourcePose({"bs.eye_close_L": 0.8, "bs.mouth_left": 0.6}, {"bone_eye_L": BoneOffset(t=(0.0, 0.05, 0.0), r=(0.0, 0.1736482, 0.0, 0.9848078))}))
    summ = autofill.generate_from_keys(doc)
    n_points = len(doc.layers[0].points)
    pre_j = joint_state(scene.mesh_joints([face]))
    node = scene.primary_blend_shape(face)
    pre_non_fc = targets_digest(node, only_non_fc=True)
    t1 = time.perf_counter()
    rep = bakemod.bake(doc)
    dt = time.perf_counter() - t1
    INFO.append(f"shizuku: ノード {node}、点 {n_points}（キー 2 + 自動生成）、作成 {len(rep.created)}、置き換え {len(rep.replaced)}、"
                f"頂点の合計 {rep.total_vertices}、捨てた {rep.culled_vertices}、空 {len(rep.empty)}、ベイク {dt:.2f} 秒（全体 {time.perf_counter() - t0:.2f} 秒）、"
                f"欠けたシェイプ {rep.missing_curves}、ボーン {rep.missing_bones}、警告 {rep.warnings}")
    check("shizuku: 5 × 3 の格子を焼ける（作成 = 点の数）", len(rep.created) == n_points and not rep.missing_curves and not rep.missing_bones, rep.summary())
    check("shizuku: 既存ターゲットが不変・ジョイントの姿勢が元のまま", targets_digest(node, only_non_fc=True) == pre_non_fc and joint_state(scene.mesh_joints([face])) == pre_j)
    state = scene.bake_state_for(face)
    issues = validate.validate(doc, scene.build_scene_info(doc), bake_state=state)
    check("shizuku: 検証でベイク済み（未ベイク・変更なし）", not [i for i in issues if i.code in ("point_unbaked", "point_changed_since_bake", "baked_morph_missing")])
    # 再ベイクで増えない
    n_idx = len(scene.target_indices(node))
    rep2 = bakemod.bake(doc)
    check("shizuku: 再ベイクは置き換えのみ", not rep2.created and len(rep2.replaced) == n_points and len(scene.target_indices(node)) == n_idx)
    # 形の確認（キー点）
    key = doc.layers[0].points[(1, 4)]
    name = naming.morph_name("shizuku", "Neutral", 1, 4)
    with scene.enter_reference_pose([face], extra_joints=["bone_eye_L"]) as ref:
        pose_apply.apply_pose(doc, key.pose, ref)
        P = scene.read_points(face)
        pose_apply.reset_to_reference(ref)
        cmds.setAttr(f"{node}.{name}", 1.0)
        Q = scene.read_points(face)
    err = max_diff(P, Q)
    check("shizuku: 焼いた FC_* = 1 がキーのポーズと同じ形（1e-3 cm）", err < 1.001e-3, f"{err}")
    cmds.file(new=True, force=True)  # shizuku を閉じる（保存しない）


def main() -> None:
    run_mini()
    mb = REPO / "assets" / "shizuku" / "shizuku.mb"
    if mb.exists() and os.environ.get("TDRIVE_FACIAL_SHIZUKU", "1") != "0":
        run_shizuku()
    else:
        INFO.append("shizuku: なし / TDRIVE_FACIAL_SHIZUKU=0 のため第 2 部を飛ばした")


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    try:
        main()
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
