"""検証・似た名前の候補・一括改名（core/validate.py）。各コードに「出る」「出ない」のテストを置く。"""

import copy
import random

import pytest

from tdrive_facial.core import model as m
from tdrive_facial.core import naming
from tdrive_facial.core import profile as P
from tdrive_facial.core import validate as V

MORPH = "FC_a_Neutral_R1_C2"


def make_doc() -> m.Document:
    """何も検出されない小さなドキュメント（asset a、Neutral の 1 点、左右の対のシェイプ、ボーン 1 本）。"""
    doc = m.Document()
    doc.asset = "a"
    doc.grid.base_bone = "head"
    doc.layers[0].points[(1, 2)] = m.GridPoint(
        1,
        2,
        True,
        m.SourcePose(
            curves={"bs.smile_L": 0.5, "bs.smile_R": 0.4},
            bones={"bone_eye_L": m.BoneOffset(t=(0.0, 0.1, 0.0)), "bone_eye_R": m.BoneOffset()},
        ),
    )
    return doc


def make_scene(doc: m.Document = None) -> V.SceneInfo:
    doc = doc or make_doc()
    return V.SceneInfo(
        curves={"bs.smile_L", "bs.smile_R", "bs.jaw_open"},
        bones={"head", "bone_eye_L", "bone_eye_R"},
        bone_parents={"head": "neck", "bone_eye_L": "head", "bone_eye_R": "head"},
        targets={MORPH},
        recorded_bone_parents={"head": "neck", "bone_eye_L": "head"},
    )


def make_bake(doc: m.Document = None) -> dict:
    doc = doc or make_doc()
    if not doc.layers or (1, 2) not in doc.layers[0].points:
        return {}
    return {MORPH: V.pose_hash(doc.layers[0].points[(1, 2)].pose)}


def run(doc=None, scene=None, profile=None, bake=...):
    doc = doc or make_doc()
    scene = scene or make_scene(doc)
    return V.validate(doc, scene, profile, make_bake(doc) if bake is ... else bake)


def codes(issues):
    return [i.code for i in issues]


def find(issues, code):
    got = [i for i in issues if i.code == code]
    assert got, f"{code} が出ていない: {codes(issues)}"
    return got


def test_clean_document_has_no_issues():
    assert run() == []


def test_all_issues_have_japanese_message_and_valid_severity():
    doc = make_doc()
    doc.grid.cols = 1
    doc.layers[0].name = "x"
    issues = run(doc)
    assert issues
    for i in issues:
        assert i.severity in ("error", "warning", "info") and i.message


# ---------------------------------------------------------------------------
# 構造（asset・格子・レイヤー）
# ---------------------------------------------------------------------------


def test_asset_missing():
    doc = make_doc()
    doc.asset = None
    issue = find(run(doc), "asset_missing")[0]
    assert issue.severity == "error"
    assert "asset_missing" not in codes(run())


@pytest.mark.parametrize("cols,rows,expect", [(1, 3, True), (5, 1, True), (2, 2, False), (5, 3, False)])
def test_grid_size_invalid(cols, rows, expect):
    doc = make_doc()
    doc.grid.cols, doc.grid.rows = cols, rows
    issues = run(doc)
    assert ("grid_size_invalid" in codes(issues)) is expect
    if expect:
        assert find(issues, "grid_size_invalid")[0].severity == "error"


def test_grid_range_invalid():
    doc = make_doc()
    doc.grid.yaw_range = 0
    assert find(run(doc), "grid_range_invalid")[0].severity == "warning"
    assert "grid_range_invalid" not in codes(run())


@pytest.mark.parametrize(
    "axis,up,expect",
    [("+X", "Z", False), ("-Y", "Z", False), ("+Z", "Y", False), ("+Z", "Z", True), ("-Y", "Y", True), ("up", "Z", True), ("X", "Z", True)],
)
def test_forward_axis_invalid(axis, up, expect):
    doc = make_doc()
    doc.grid.forward_axis = axis
    doc.meta.up_axis = up
    issues = run(doc)
    assert ("forward_axis_invalid" in codes(issues)) is expect
    if expect:
        assert find(issues, "forward_axis_invalid")[0].severity == "error"


def test_base_bone_missing_and_case_mismatch():
    doc = make_doc()
    doc.grid.base_bone = "Head"
    issues = run(doc)
    cm = find(issues, "bone_case_mismatch")[0]
    assert cm.name == "Head" and cm.suggestion == "head" and "base_bone_missing" not in codes(issues)
    doc.grid.base_bone = "neck_bone"
    assert find(run(doc), "base_bone_missing")[0].severity == "error"
    doc.grid.base_bone = ""
    assert "base_bone_missing" in codes(run(doc))
    assert "base_bone_missing" not in codes(run())


def test_base_bone_skipped_when_bones_unknown():
    doc = make_doc()
    doc.grid.base_bone = "nope"
    scene = make_scene(doc)
    scene.bones = None
    assert "base_bone_missing" not in codes(run(doc, scene))


def test_layer0_not_neutral():
    doc = make_doc()
    doc.layers[0].name = "Base"
    issue = find(run(doc), "layer0_not_neutral")[0]
    assert issue.severity == "error" and issue.suggestion == "Neutral" and issue.layer == 0
    doc.layers = []
    assert "layer0_not_neutral" in codes(run(doc))
    assert "layer0_not_neutral" not in codes(run())


def test_layer_count_exceeded():
    doc = make_doc()
    doc.layers += [m.Layer(name=f"L{i}") for i in range(1, 16)]  # 16 個は OK
    assert "layer_count_exceeded" not in codes(run(doc))
    doc.layers.append(m.Layer(name="L16"))
    assert find(run(doc), "layer_count_exceeded")[0].severity == "error"


def test_layer_name_empty_chars_reserved():
    doc = make_doc()
    doc.layers += [m.Layer(name=""), m.Layer(name="喜び"), m.Layer(name="Persp"), m.Layer(name="Joy_2")]
    issues = run(doc)
    assert find(issues, "layer_name_empty")[0].layer == 1
    assert find(issues, "layer_name_chars")[0].layer == 2
    assert find(issues, "layer_name_reserved")[0].layer == 3
    assert [i.layer for i in issues if i.code == "layer_name_chars"] == [2]  # Joy_2 は問題なし
    assert not {"layer_name_empty", "layer_name_chars", "layer_name_reserved"} & set(codes(run()))


def test_layer_name_duplicate_reported_once():
    doc = make_doc()
    doc.layers += [m.Layer(name="Joy"), m.Layer(name="Joy"), m.Layer(name="Joy")]
    got = find(run(doc), "layer_name_duplicate")
    assert len(got) == 1 and got[0].severity == "error" and got[0].name == "Joy"
    assert "layer_name_duplicate" not in codes(run())


def test_layer_name_case_collision():
    doc = make_doc()
    doc.layers += [m.Layer(name="Joy"), m.Layer(name="joy")]
    got = find(run(doc), "layer_name_case_collision")
    assert got[0].severity == "warning" and got[0].layer == 2
    assert "layer_name_duplicate" not in codes(run(doc))
    doc.layers[2].name = "Anger"
    assert "layer_name_case_collision" not in codes(run(doc))


def test_layer_empty():
    doc = make_doc()
    doc.layers.append(m.Layer(name="Joy"))
    doc.layers.append(m.Layer(name="Sad", points={(0, 0): m.GridPoint(0, 0, True, m.SourcePose())}))  # 空のポーズだけ
    got = [i for i in run(doc) if i.code == "layer_empty"]
    assert [i.layer for i in got] == [1, 2] and got[0].severity == "info"
    assert "layer_empty" not in codes(run())


def test_point_outside_grid():
    doc = make_doc()
    doc.layers[0].points[(3, 0)] = m.GridPoint(3, 0, True, m.SourcePose(curves={"bs.jaw_open": 1.0}))
    got = find(run(doc), "point_outside_grid")
    assert (got[0].row, got[0].col) == (3, 0)
    # 格子の外の点はベイクの検査の対象外（未ベイクにもならない）
    assert all(i.row != 3 for i in run(doc) if i.code == "point_unbaked")
    assert "point_outside_grid" not in codes(run())


@pytest.mark.parametrize("prefix,expect", [("fcs_", False), ("sc_", False), ("", True), ("FC_x", True)])
def test_sculpt_prefix_invalid(prefix, expect):
    doc = make_doc()
    doc.sculpt_shapes = m.SculptShapes(prefix=prefix)
    assert ("sculpt_prefix_invalid" in codes(run(doc))) is expect


# ---------------------------------------------------------------------------
# 参照（シェイプ・ボーン）
# ---------------------------------------------------------------------------


def test_curve_missing_with_suggestions_and_location():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.jaw_opn"] = 0.3
    issue = find(run(doc), "curve_missing")[0]
    assert issue.severity == "warning" and issue.name == "bs.jaw_opn"
    assert (issue.layer, issue.row, issue.col) == (0, 1, 2)
    assert issue.suggestion == "bs.jaw_open" and issue.candidates[0] == "bs.jaw_open"
    assert "curve_missing" not in codes(run())


def test_curve_missing_aggregated_per_name_with_count():
    doc = make_doc()
    doc.layers[0].points[(0, 0)] = m.GridPoint(0, 0, True, m.SourcePose(curves={"bs.ghost": 1.0}))
    doc.layers[0].points[(0, 1)] = m.GridPoint(0, 1, True, m.SourcePose(curves={"bs.ghost": 1.0}))
    doc.working_set.curves = ["bs.ghost"]
    got = find(run(doc), "curve_missing")
    assert len(got) == 1 and "3 か所" in got[0].message


def test_curve_case_mismatch_is_not_also_missing():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["BS.Jaw_Open"] = 0.3
    issues = run(doc)
    cm = find(issues, "curve_case_mismatch")[0]
    assert cm.suggestion == "bs.jaw_open" and cm.name == "BS.Jaw_Open"
    assert "curve_missing" not in codes(issues)


def test_curve_refs_in_working_set_and_intensity_curves():
    doc = make_doc()
    doc.working_set.curves = ["bs.smile_L", "bs.nowhere"]
    doc.intensity_curves = ["bs.nope"]
    names = {i.name for i in run(doc) if i.code == "curve_missing"}
    assert names == {"bs.nowhere", "bs.nope"}
    assert all(i.layer is None for i in run(doc) if i.code == "curve_missing")


def test_bone_missing_and_case_mismatch():
    doc = make_doc()
    pose = doc.layers[0].points[(1, 2)].pose
    pose.bones["bone_mouth"] = m.BoneOffset()
    pose.bones["BONE_EYE_L"] = m.BoneOffset()
    doc.working_set.bones = ["bone_hand"]
    issues = run(doc)
    assert {i.name for i in issues if i.code == "bone_missing"} == {"bone_mouth", "bone_hand"}
    assert find(issues, "bone_case_mismatch")[0].suggestion == "bone_eye_L"
    assert "bone_missing" not in codes(run())


def test_missing_checks_skipped_when_scene_lists_unknown():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.ghost"] = 1.0
    scene = make_scene(doc)
    scene.curves = None
    assert not {"curve_missing", "mirror_partner_missing", "profile_standard_missing"} & set(codes(run(doc, scene)))
    scene.curves = set()  # 空 = 「無い」と確かめた
    assert "curve_missing" in codes(run(doc, scene))


# --- ミラー ---


def test_mirror_partner_missing_for_curve_and_bone():
    doc = make_doc()
    scene = make_scene(doc)
    scene.curves = {"bs.smile_L", "bs.jaw_open"}  # _R が無い
    scene.bones = {"head", "bone_eye_L", "bone_eye_R"}
    issues = run(doc, scene)
    got = find(issues, "mirror_partner_missing")
    assert [i.name for i in got] == ["bs.smile_L"] and got[0].suggestion == "bs.smile_R"
    # bs.smile_R は参照されているがモデルに無い → missing（ミラー相手の検査は「ある名前」だけ）
    assert any(i.code == "curve_missing" and i.name == "bs.smile_R" for i in issues)
    scene.bones = {"head", "bone_eye_L"}
    assert any(i.code == "mirror_partner_missing" and i.name == "bone_eye_L" for i in run(doc, scene))
    assert "mirror_partner_missing" not in codes(run())


def test_mirror_partner_missing_honours_exclude_and_disabled():
    doc = make_doc()
    scene = make_scene(doc)
    scene.curves = {"bs.smile_L", "bs.smile_R", "bs.jaw_open"}
    scene.bones = {"head", "bone_eye_L", "bone_eye_R"}
    doc.layers[0].points[(1, 2)].pose.curves["bs.brow_L"] = 0.2
    scene.curves.add("bs.brow_L")
    assert "mirror_partner_missing" in codes(run(doc, scene))
    doc.mirror.exclude = ["brow"]  # 部分一致
    assert "mirror_partner_missing" not in codes(run(doc, scene))
    doc.mirror.exclude = []
    prof = P.NamingProfile(name="p", mirror=P.MirrorRule(exclude=["brow_"]))
    assert "mirror_partner_missing" not in codes(run(doc, scene, prof))  # プロファイルの除外も効く
    doc.mirror.enabled = False
    assert "mirror_partner_missing" not in codes(run(doc, scene))


def test_mirror_partner_with_arkit_suffix():
    doc = make_doc()
    doc.mirror.suffix_l, doc.mirror.suffix_r = "Left", "Right"
    doc.layers[0].points[(1, 2)].pose = m.SourcePose(curves={"eyeBlinkLeft": 1.0})
    scene = make_scene(doc)
    scene.curves = {"eyeBlinkLeft"}
    scene.bones = {"head"}
    got = find(run(doc, scene), "mirror_partner_missing")
    assert got[0].suggestion == "eyeBlinkRight"


def test_mirror_suffix_invalid():
    doc = make_doc()
    doc.mirror.suffix_r = doc.mirror.suffix_l
    assert "mirror_suffix_invalid" in codes(run(doc))
    doc.mirror.enabled = False
    assert "mirror_suffix_invalid" not in codes(run(doc))
    assert "mirror_suffix_invalid" not in codes(run())


# --- ボーン階層（UE 版）---


def test_bone_parent_changed():
    doc = make_doc()
    scene = make_scene(doc)
    scene.bone_parents = dict(scene.bone_parents, bone_eye_L="neck")
    issue = find(run(doc, scene), "bone_parent_changed")[0]
    assert issue.name == "bone_eye_L" and issue.severity == "warning"
    assert "bone_parent_changed" not in codes(run())
    scene.recorded_bone_parents = None  # 記録が無ければ検査しない
    assert "bone_parent_changed" not in codes(run(doc, scene))


# ---------------------------------------------------------------------------
# 可動域
# ---------------------------------------------------------------------------


def test_limit_exceeded_default_range_and_hint():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 1.5
    got = find(run(doc), "limit_exceeded")
    assert got[0].name == "bs.smile_L" and (got[0].row, got[0].col) == (1, 2) and "誇張" in got[0].message
    assert "limit_exceeded" not in codes(run())


def test_limit_exceeded_follows_doc_and_profile_limits():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 1.5
    doc.limits = {"bs.smile_L": (0.0, 2.0)}
    assert "limit_exceeded" not in codes(run(doc))
    doc.limits = None
    prof = P.NamingProfile(name="p", limits={"bs.smile_L": (0.0, 2.0)})
    assert "limit_exceeded" not in codes(run(doc, profile=prof))
    doc.limits = {"bs.smile_L": (0.0, 1.2)}  # ドキュメントが勝つ
    assert "limit_exceeded" in codes(run(doc, profile=prof))
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = -0.1
    doc.limits = None
    assert "limit_exceeded" in codes(run(doc))


def test_limit_invalid():
    doc = make_doc()
    doc.limits = {"bs.smile_L": (1.0, 0.0)}
    assert find(run(doc), "limit_invalid")[0].severity == "error"
    doc.limits = {"bs.smile_L": (0.0, float("inf"))}
    assert "limit_invalid" in codes(run(doc))
    prof = P.NamingProfile(name="p", limits={"x": (2.0, 1.0)})
    doc.limits = None
    assert "limit_invalid" in codes(run(doc, profile=prof))
    doc.limits = {"bs.smile_L": (0.0, 2.0)}
    assert "limit_invalid" not in codes(run(doc))


# --- 補正除外（UE 版 R-17）---


def test_excluded_in_pose():
    doc = make_doc()
    doc.exclude.curves = ["smile"]
    doc.exclude.bones = ["eye_R"]
    issues = [i for i in run(doc) if i.code == "excluded_in_pose"]
    assert {i.name for i in issues} == {"bs.smile_L", "bs.smile_R", "bone_eye_R"}
    assert issues[0].severity == "info"
    assert "excluded_in_pose" not in codes(run())
    doc.layers[0].points[(1, 2)].pose.curves = {"bs.smile_L": 0.0, "bs.smile_R": 0.0}  # 重み 0 は対象外（UE 版と同じ）
    doc.exclude.bones = []
    assert "excluded_in_pose" not in codes(run(doc))


# ---------------------------------------------------------------------------
# プロファイルの標準シェイプ
# ---------------------------------------------------------------------------


def test_profile_standard_missing():
    prof = P.NamingProfile(name="p", standard_curves=["bs.smile_L", "bs.jaw_opn", "bs.cheek"])
    got = [i for i in run(profile=prof) if i.code == "profile_standard_missing"]
    assert [i.name for i in got] == ["bs.jaw_opn", "bs.cheek"] and got[0].severity == "info"
    assert got[0].suggestion == "bs.jaw_open"
    prof.standard_curves = ["bs.smile_L"]
    assert "profile_standard_missing" not in codes(run(profile=prof))
    assert "profile_standard_missing" not in codes(run())  # プロファイル無し


# ---------------------------------------------------------------------------
# ベイク・ターゲット
# ---------------------------------------------------------------------------


def test_point_unbaked():
    got = find(run(bake={}), "point_unbaked")
    assert (got[0].layer, got[0].row, got[0].col, got[0].name) == (0, 1, 2, MORPH) and got[0].severity == "info"
    assert "point_unbaked" not in codes(run())
    assert not {"point_unbaked", "point_changed_since_bake"} & set(codes(run(bake=None)))  # None = 不明


def test_unbaked_ignores_empty_pose_points():
    doc = make_doc()
    doc.layers[0].points[(0, 0)] = m.GridPoint(0, 0, False, m.SourcePose())
    assert "point_unbaked" not in codes(run(doc))


def test_point_changed_since_bake_and_baked_morph_missing():
    doc = make_doc()
    bake = make_bake(doc)
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 0.6
    got = find(run(doc, bake=bake), "point_changed_since_bake")
    assert got[0].severity == "warning" and got[0].name == MORPH
    # 丸め誤差の範囲（1e-6 未満）の変化は変更とみなさない
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 0.6 + 1e-8
    assert "point_changed_since_bake" not in codes(run(doc, bake={MORPH: V.pose_hash(doc.layers[0].points[(1, 2)].pose)}))
    # ベイク済みのはずなのにシェイプが無い
    doc = make_doc()
    scene = make_scene(doc)
    scene.targets = set()
    issues = run(doc, scene)
    assert find(issues, "baked_morph_missing")[0].severity == "warning"
    assert "point_changed_since_bake" not in codes(issues)
    assert "baked_morph_missing" not in codes(run())


def test_orphan_targets():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 1.5  # 重み 1 超 → この点の _Ex は要る（R-37）
    doc.perspective = m.Perspective(enabled=True, keys=[{}, {}])
    scene = make_scene(doc)
    scene.targets = {
        MORPH,
        "FC_a_Neutral_R1_C2_Ex",  # 点があり、重み 1 超がある → 孤立ではない
        "FC_a_Neutral_R0_C0",  # 点が無い
        "FC_a_Joy_R1_C2",  # レイヤーが無い
        "FC_a_Persp_K1",  # キーがある
        "FC_a_Persp_K2",  # キーが無い
        "FC_a_garbage",  # 規則に合わない
        "FC_other_Neutral_R0_C0",  # 別のアセットは対象外
        "bs.mouth_smile_L",  # モデルのシェイプには触れない
    }
    got = sorted(i.name for i in run(doc, scene) if i.code == "orphan_target")
    assert got == ["FC_a_Joy_R1_C2", "FC_a_Neutral_R0_C0", "FC_a_Persp_K2", "FC_a_garbage"]
    assert "orphan_target" not in codes(run())
    # 重み 1 超が無くなった点の _Ex は孤立（ベイクで消える）。感情レイヤーは Neutral の同じ点に誇張があれば要る
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 1.0
    assert "FC_a_Neutral_R1_C2_Ex" in [i.name for i in run(doc, scene) if i.code == "orphan_target"]


def test_orphan_when_point_outside_grid_or_empty_pose():
    doc = make_doc()
    doc.layers[0].points[(4, 0)] = m.GridPoint(4, 0, True, m.SourcePose(curves={"bs.jaw_open": 1.0}))
    doc.layers[0].points[(0, 1)] = m.GridPoint(0, 1, True, m.SourcePose())
    scene = make_scene(doc)
    scene.targets = {MORPH, "FC_a_Neutral_R4_C0", "FC_a_Neutral_R0_C1"}
    assert sorted(i.name for i in run(doc, scene) if i.code == "orphan_target") == ["FC_a_Neutral_R0_C1", "FC_a_Neutral_R4_C0"]


def test_orphan_with_underscored_layer_name():
    doc = make_doc()
    doc.layers.append(m.Layer(name="Joy_Big", points={(0, 0): m.GridPoint(0, 0, True, m.SourcePose(curves={"bs.jaw_open": 1.0}))}))
    scene = make_scene(doc)
    scene.targets = {MORPH, "FC_a_Joy_Big_R0_C0", "FC_a_Joy_Big_R0_C1"}
    got = [i.name for i in run(doc, scene, bake=None) if i.code == "orphan_target"]
    assert got == ["FC_a_Joy_Big_R0_C1"]


def test_sculpt_unused():
    doc = make_doc()
    scene = make_scene(doc)
    scene.targets = {MORPH, "fcs_Neutral_R1_C2", "fcs_Neutral_R0_C0"}
    scene.curves = set(scene.curves) | {"bs.fcs_Neutral_R1_C2"}
    doc.layers[0].points[(1, 2)].pose.curves["bs.fcs_Neutral_R1_C2"] = 1.0
    got = [i.name for i in run(doc, scene, bake=None) if i.code == "sculpt_unused"]
    assert got == ["fcs_Neutral_R0_C0"]
    assert "sculpt_unused" not in codes(run())


def test_sculpt_unused_ignores_combo_shapes():
    """組み合わせ補正 `fcs_combo_*`（シェイプタブが作る。2 つのシェイプで駆動される）は「どのポーズからも使われていない」に数えない。"""
    doc = make_doc()
    scene = make_scene(doc)
    scene.targets = {MORPH, "fcs_combo_smile_L__mouth_open", "fcs_Neutral_R0_C0"}
    got = [i.name for i in run(doc, scene, bake=None) if i.code == "sculpt_unused"]
    assert got == ["fcs_Neutral_R0_C0"]
    assert naming.is_combo_name("fcs_combo_a__b") and not naming.is_combo_name("fcs_Neutral_R0_C0") and not naming.is_combo_name("FC_combo_a")


def test_target_empty():
    doc = make_doc()
    scene = make_scene(doc)
    scene.target_info = {MORPH: V.TargetInfo(vertex_count=0, empty=True), "FC_a_X_R0_C0": V.TargetInfo(vertex_count=5)}
    got = find(run(doc, scene), "target_empty")
    assert [i.name for i in got] == [MORPH] and got[0].severity == "info"
    assert "target_empty" not in codes(run())


def test_validate_does_not_modify_inputs():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.ghost"] = 2.0
    before = copy.deepcopy(doc)
    scene = make_scene(doc)
    V.validate(doc, scene, None, {})
    assert doc == before


# ---------------------------------------------------------------------------
# pose_hash
# ---------------------------------------------------------------------------


def test_pose_hash_is_order_independent_and_rounds():
    a = m.SourcePose(curves={"x": 0.5, "y": 0.25}, bones={"b": m.BoneOffset(t=(1.0, 2.0, 3.0))})
    b = m.SourcePose(curves={"y": 0.25, "x": 0.5}, bones={"b": m.BoneOffset(t=(1.0, 2.0, 3.0))})
    assert V.pose_hash(a) == V.pose_hash(b)
    c = m.SourcePose(curves={"x": 0.5 + 4e-7, "y": 0.25 - 4e-7}, bones={"b": m.BoneOffset(t=(1.0 + 4e-7, 2.0, 3.0))})
    assert V.pose_hash(a) == V.pose_hash(c)  # 1e-6 未満は同じ
    d = m.SourcePose(curves={"x": 0.5 + 3e-6, "y": 0.25}, bones=a.bones)
    assert V.pose_hash(a) != V.pose_hash(d)


def test_pose_hash_distinguishes_names_values_and_bones():
    base = m.SourcePose(curves={"x": 0.5})
    assert V.pose_hash(base) != V.pose_hash(m.SourcePose(curves={"y": 0.5}))
    assert V.pose_hash(base) != V.pose_hash(m.SourcePose(curves={"x": 0.6}))
    assert V.pose_hash(base) != V.pose_hash(m.SourcePose(curves={"x": 0.5}, bones={"b": m.BoneOffset(t=(0.0, 0.0, 1.0))}))
    assert V.pose_hash(m.SourcePose(bones={"b": m.BoneOffset(r=(0.0, 0.0, 0.7071068, 0.7071068))})) != V.pose_hash(
        m.SourcePose(bones={"b": m.BoneOffset()})
    )


def test_pose_hash_ignores_zero_weights_and_identity_bones():
    assert V.pose_hash(m.SourcePose()) == V.pose_hash(
        m.SourcePose(curves={"x": 0.0, "z": -1e-9}, bones={"b": m.BoneOffset()})
    )


def test_pose_hash_is_stable_across_runs():
    # 値を固定（保存済みの bake_state が将来も読めるように。式を変えるなら MAJOR）
    pose = m.SourcePose(curves={"bs.a": 0.5}, bones={"bone_x": m.BoneOffset(t=(0.0, 1.0, 0.0))})
    assert V.pose_hash(pose) == "79814c726439a7bace73f9453ae0d0b8ce13dffe"
    keys = list("abcdefgh")
    random.Random(1).shuffle(keys)
    assert V.pose_hash(m.SourcePose(curves={k: 0.1 for k in keys})) == V.pose_hash(
        m.SourcePose(curves={k: 0.1 for k in sorted(keys)})
    )


# ---------------------------------------------------------------------------
# 似た名前の候補
# ---------------------------------------------------------------------------


def test_levenshtein():
    assert V.levenshtein("", "abc") == 3 and V.levenshtein("abc", "") == 3
    assert V.levenshtein("kitten", "sitting") == 3
    assert V.levenshtein("same", "same") == 0


CANDS = ["bs.mouth_smile_L", "bs.mouth_smile_R", "bs.mouth_frown_L", "bs.jaw_open", "bs.eye_close_L", "bs.eye_close_R"]


def test_suggest_names_ranks_by_edit_distance_case_insensitive():
    assert V.suggest_names("bs.mouth_smile_l", CANDS)[0] == "bs.mouth_smile_L"  # 大小違いは距離 0
    assert V.suggest_names("bs.mouth_smle_L", CANDS, limit=2) == ["bs.mouth_smile_L", "bs.mouth_smile_R"]
    assert V.suggest_names("bs.eye_clsoe_R", CANDS)[0] == "bs.eye_close_R"


def test_suggest_names_limit_and_cutoff():
    assert len(V.suggest_names("bs.mouth_smile_L", CANDS, limit=3)) == 3
    assert V.suggest_names("bs.mouth_smile_L", CANDS, limit=0) == []
    # UE 版の打ち切り: 距離 > max(len, 4) // 2 + 2 は無関係
    assert V.suggest_names("zzzzzzzzzz", CANDS) == []
    assert V.suggest_names("ab", ["abcdefgh"]) == []  # len 4 → 閾値 4。距離 6
    assert V.suggest_names("ab", ["abcd"]) == ["abcd"]  # 距離 2 ≦ 4


def test_suggest_names_ties_are_deterministic():
    cands = ["bs.a_L", "bs.a_R", "bs.a_C"]
    first = V.suggest_names("bs.a_X", cands)
    assert first == V.suggest_names("bs.a_X", cands) and set(first) == set(cands)


def test_case_only_match():
    assert V.case_only_match("BS.JAW_OPEN", CANDS) == "bs.jaw_open"
    assert V.case_only_match("bs.jaw_open", CANDS) == ""  # 完全一致は対象外
    assert V.case_only_match("nothing", CANDS) == ""


# ---------------------------------------------------------------------------
# 一括改名
# ---------------------------------------------------------------------------


def rich_doc() -> m.Document:
    doc = make_doc()
    doc.layers[0].emotion_curve = "emo_curve"
    doc.layers.append(
        m.Layer(
            name="Joy",
            emotion_curve="bs.smile_L",
            points={(0, 0): m.GridPoint(0, 0, True, m.SourcePose(curves={"bs.smile_L": 1.0, "bs.jaw": 0.2}, bones={"bone_eye_L": m.BoneOffset()}))},
        )
    )
    doc.working_set = m.WorkingSet(curves=["bs.smile_L", "bs.smile_R"], bones=["bone_eye_L", "head"])
    doc.intensity_curves = ["bs.smile_L"]
    doc.limits = {"bs.smile_L": (0.0, 2.0), "bs.smile_R": (0.0, 1.0)}
    doc.mirror.exclude = ["bs.smile_L", "mouth"]
    doc.exclude.curves = ["bs.smile_L"]
    doc.exclude.bones = ["bone_eye_L"]
    return doc


def test_rename_curves_everywhere():
    doc = rich_doc()
    n = V.rename_references(doc, {"bs.smile_L": "bs.mouth_smile_L"}, "curve")
    p1 = doc.layers[0].points[(1, 2)].pose.curves
    p2 = doc.layers[1].points[(0, 0)].pose.curves
    assert "bs.mouth_smile_L" in p1 and "bs.smile_L" not in p1 and p1["bs.mouth_smile_L"] == 0.5
    assert p2 == {"bs.mouth_smile_L": 1.0, "bs.jaw": 0.2}
    assert list(p2) == ["bs.mouth_smile_L", "bs.jaw"]  # 順は保つ
    assert doc.working_set.curves == ["bs.mouth_smile_L", "bs.smile_R"]
    assert doc.intensity_curves == ["bs.mouth_smile_L"]
    assert doc.layers[1].emotion_curve == "bs.mouth_smile_L"
    assert doc.limits == {"bs.mouth_smile_L": (0.0, 2.0), "bs.smile_R": (0.0, 1.0)}
    assert doc.mirror.exclude == ["bs.mouth_smile_L", "mouth"]  # 完全一致だけ置換（部分一致用の語は触らない）
    assert doc.exclude.curves == ["bs.mouth_smile_L"]
    assert doc.exclude.bones == ["bone_eye_L"]  # bone 側は触らない
    assert doc.layers[0].points[(1, 2)].pose.bones.keys() == {"bone_eye_L", "bone_eye_R"}
    # 点 2 + 作業セット 1 + 強度 1 + 感情 1 + limits 1 + 除外 1 + ミラー除外 1
    assert n == 8


def test_rename_report_breakdown():
    doc = rich_doc()
    rep = V.rename_report(doc, {"bs.smile_L": "bs.mouth_smile_L"}, "curve")
    assert rep.curve_replacements == 2 and rep.bone_replacements == 0
    assert rep.working_set_replacements == 1 and rep.policy_replacements == 5 and rep.collisions == 0
    assert rep.total == 8


def test_rename_bones_everywhere_including_base_bone():
    doc = rich_doc()
    doc.grid.base_bone = "head"
    n = V.rename_references(doc, {"bone_eye_L": "eye_L", "head": "bone_head"}, "bone")
    assert doc.layers[0].points[(1, 2)].pose.bones.keys() == {"eye_L", "bone_eye_R"}
    assert doc.layers[1].points[(0, 0)].pose.bones.keys() == {"eye_L"}
    assert doc.working_set.bones == ["eye_L", "bone_head"]
    assert doc.grid.base_bone == "bone_head"
    assert doc.exclude.bones == ["eye_L"]
    assert doc.working_set.curves == ["bs.smile_L", "bs.smile_R"]  # curve 側は触らない
    assert n == 2 + 2 + 1 + 1  # 点 2、作業セット 2、基準ボーン 1、除外 1


def test_rename_collision_keeps_values_in_that_pose():
    doc = make_doc()
    pose = doc.layers[0].points[(1, 2)].pose
    pose.curves = {"smile_l": 0.3, "smile_L": 0.7, "other": 0.1}
    rep = V.rename_report(doc, {"smile_l": "smile_L"}, "curve")
    assert pose.curves == {"smile_l": 0.3, "smile_L": 0.7, "other": 0.1}  # 値を失わない
    assert rep.collisions == 1 and rep.curve_replacements == 0


def test_rename_swap_is_simultaneous_not_chained():
    doc = make_doc()
    pose = doc.layers[0].points[(1, 2)].pose
    pose.curves = {"a": 1.0, "b": 2.0}
    assert V.rename_references(doc, {"a": "b", "b": "a"}, "curve") == 2
    assert pose.curves == {"b": 1.0, "a": 2.0}


def test_rename_ignores_noop_and_empty_mapping_and_bad_kind():
    doc = rich_doc()
    before = copy.deepcopy(doc)
    assert V.rename_references(doc, {}, "curve") == 0
    assert V.rename_references(doc, {"x": "x", "": "y", "z": ""}, "curve") == 0
    assert doc == before
    with pytest.raises(ValueError):
        V.rename_references(doc, {"a": "b"}, "mesh")


def test_rename_dedupes_working_set_duplicates():
    doc = make_doc()
    doc.working_set.curves = ["a", "b"]
    V.rename_references(doc, {"a": "b"}, "curve")
    assert doc.working_set.curves == ["b"]


def test_rename_fixes_validation_issue_end_to_end():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.jaw_opn"] = 0.3
    scene = make_scene(doc)
    issue = find(V.validate(doc, scene), "curve_missing")[0]
    assert V.rename_references(doc, {issue.name: issue.suggestion}, "curve") == 1
    assert "curve_missing" not in codes(V.validate(doc, scene))


# --- 消えた参照の削除 ---


def test_remove_missing_references():
    doc = rich_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.ghost"] = 1.0
    doc.layers[0].points[(1, 2)].pose.bones["bone_ghost"] = m.BoneOffset()
    doc.working_set.curves.append("bs.ghost")
    doc.working_set.bones.append("bone_ghost")
    doc.intensity_curves.append("bs.ghost")
    scene = make_scene(doc)
    scene.curves = set(scene.curves) | {"bs.jaw"}
    assert V.remove_missing_references(doc, scene) == 5
    assert "bs.ghost" not in doc.layers[0].points[(1, 2)].pose.curves
    assert "bone_ghost" not in doc.layers[0].points[(1, 2)].pose.bones
    assert doc.working_set.curves == ["bs.smile_L", "bs.smile_R"] and doc.working_set.bones == ["bone_eye_L", "head"]
    assert doc.intensity_curves == ["bs.smile_L"]
    assert V.remove_missing_references(doc, scene) == 0
    assert doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] == 0.5  # 残った値はそのまま


def test_remove_missing_keeps_case_mismatch_unless_asked():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["BS.JAW_OPEN"] = 0.3
    scene = make_scene(doc)
    assert V.remove_missing_references(doc, scene) == 0
    assert V.remove_missing_references(doc, scene, include_case_mismatch=True) == 1


def test_remove_missing_skips_unknown_lists():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.ghost"] = 1.0
    assert V.remove_missing_references(doc, V.SceneInfo()) == 0
    assert "bs.ghost" in doc.layers[0].points[(1, 2)].pose.curves


# ---------------------------------------------------------------------------
# 命名規則との整合
# ---------------------------------------------------------------------------


def test_morph_names_used_for_bake_checks_follow_naming_module():
    doc = make_doc()
    assert naming.morph_name(doc.asset, "Neutral", 1, 2) == MORPH
    assert [i.name for i in run(doc, bake={})] == [MORPH]


def test_orphan_ignores_targets_of_another_asset_that_extends_this_asset_id():
    """C-1 / S-8: asset a のデータを検証するとき、asset a_b（a で始まる別のアセット）のターゲットを孤立と言わない。"""
    doc = make_doc()
    scene = make_scene(doc)
    scene.targets = {MORPH, "FC_a_b_Neutral_R1_C2", "FC_a_b_Joy_Big_R0_C0", "FC_a_b_Persp_K0", "FC_a_Gone_R0_C0"}
    got = sorted(i.name for i in run(doc, scene) if i.code == "orphan_target")
    assert got == ["FC_a_Gone_R0_C0"]


# ---------------------------------------------------------------- docs/19 C-4 / C-5: 検証が見ていない値
def _issue(doc, code):
    return [i for i in run(doc, make_scene(doc), bake=None) if i.code == code]


def test_non_finite_values_are_errors():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = float("nan")
    doc.grid.center_offset = (0.0, float("inf"), 0.0)
    got = _issue(doc, "non_finite_value")
    assert got and all(i.severity == "error" for i in got)
    assert any("smile_L" in i.message for i in got) and any("centerOffset" in i.message for i in got)
    assert not _issue(make_doc(), "non_finite_value")


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: setattr(d.mirror, "bone_axis", "x"), "mirror_axis_invalid"),
        (lambda d: setattr(d.mirror, "bone_axis", "W"), "mirror_axis_invalid"),
        (lambda d: setattr(d.autogen, "mode", "Nearest"), "autogen_invalid"),
        (lambda d: setattr(d.autogen, "idw_power", 0.0), "autogen_invalid"),
        (lambda d: setattr(d.autogen, "idw_power", 500.0), "autogen_invalid"),
        (lambda d: setattr(d.meta, "unit", "furlong"), "meta_invalid"),
        (lambda d: setattr(d.meta, "up_axis", "X"), "meta_invalid"),
        (lambda d: (setattr(d.grid, "cols", 1000), setattr(d.grid, "rows", 1000)), "grid_size_invalid"),
    ],
)
def test_values_that_would_crash_later_are_errors(mutate, code):
    doc = make_doc()
    mutate(doc)
    got = _issue(doc, code)
    assert got and got[0].severity == "error"


def test_valid_defaults_have_no_value_errors():
    doc = make_doc()
    doc.mirror.bone_axis = "X"
    codes_ = codes(run(doc, make_scene(doc)))
    assert not {"non_finite_value", "mirror_axis_invalid", "autogen_invalid", "meta_invalid"} & set(codes_)


def test_rename_chain_does_not_lose_values():
    """C-3: A→B と C→A のように連鎖する改名で、同じポーズの値が上書きされて消えない。"""
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves = {"A": 1.0, "B": 2.0, "C": 3.0}
    rep = V.rename_report(doc, {"A": "B", "C": "A"}, "curve")
    got = doc.layers[0].points[(1, 2)].pose.curves
    assert sorted(got.values()) == [1.0, 2.0, 3.0]  # 値は 1 つも失われない
    assert rep.collisions >= 1
    # 重ならない連鎖（B がポーズに無い）は適用できる
    doc.layers[0].points[(1, 2)].pose.curves = {"A": 1.0, "C": 3.0}
    V.rename_report(doc, {"A": "B", "C": "A"}, "curve")
    assert doc.layers[0].points[(1, 2)].pose.curves == {"B": 1.0, "A": 3.0}


# ---------------------------------------------------------------------------
# 除外パターンを変えたあとの「変更あり」（記録した指紋と今を比べる）
# ---------------------------------------------------------------------------


def test_exclude_signature_is_normalized():
    a, b = make_doc(), make_doc()
    a.exclude.curves = ["eye", " mouth ", "eye", ""]
    b.exclude.curves = ["mouth", "eye"]
    assert V.exclude_signature(a) == V.exclude_signature(b)
    b.exclude.bones = ["bone_eye"]
    assert V.exclude_signature(a) != V.exclude_signature(b)


def test_exclude_change_marks_baked_points_changed_and_revert_clears():
    doc = make_doc()
    rec = {MORPH: V.exclude_signature(doc)}
    assert run(doc, bake=make_bake(doc)) == [] and V.validate(doc, make_scene(doc), None, make_bake(doc), rec) == []
    doc.exclude.curves = ["smile_R"]
    got = find(V.validate(doc, make_scene(doc), None, make_bake(doc), rec), "point_changed_since_bake")
    assert got[0].severity == "warning" and got[0].name == MORPH and "除外" in got[0].message
    doc.exclude.curves = []  # 元に戻す
    assert "point_changed_since_bake" not in codes(V.validate(doc, make_scene(doc), None, make_bake(doc), rec))


def test_exclude_legacy_record_is_not_stale():
    doc = make_doc()
    doc.exclude.curves = ["smile_R"]
    for rec in (None, {}, {"FC_other": "x"}):  # 記録なし・この点の記録なし = 不明 → 今と同じ
        assert "point_changed_since_bake" not in codes(V.validate(doc, make_scene(doc), None, make_bake(doc), rec))
