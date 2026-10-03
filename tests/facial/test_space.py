"""座標系の変換（space.py）。軸の対応は space.py の docstring。"""

import copy
import math
import random

import pytest

from tdrive_facial.core import fcpose_io as io
from tdrive_facial.core import space as sp
from tdrive_facial.core.model import (
    BoneOffset,
    Document,
    GridPoint,
    Layer,
    Meta,
    PoseDocument,
    SourcePose,
)

SPACES = {
    "maya": sp.MAYA,
    "unity": sp.UNITY,
    "ue": sp.UE,
    "blender": sp.SpaceSpec("m", "Z", "right"),
    "maya_m": sp.SpaceSpec("m", "Y", "right"),
    "y_left_cm": sp.SpaceSpec("cm", "Y", "left"),
}
PAIRS = [(a, b) for a in SPACES for b in SPACES]
TOL = 1e-9


def rv(rng, scale=100.0):
    return [rng.uniform(-scale, scale) for _ in range(3)]


def rq(rng):
    q = [rng.uniform(-1, 1) for _ in range(4)]
    return sp.quat_normalize(q)


def close(a, b, tol=TOL):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def q_equiv(a, b, tol=TOL):
    """四元数は符号違いも同じ回転。"""
    return close(a, b, tol) or close(a, [-c for c in b], tol)


# --- 基本の対応 ---


def test_ue_is_identity():
    assert sp.convert_position([1, 2, 3], sp.UE, sp.CANONICAL) == (1, 2, 3)
    assert sp.convert_quaternion([0.1, 0.2, 0.3, 0.9], sp.UE, sp.CANONICAL) == (0.1, 0.2, 0.3, 0.9)


FORWARD_UP = {  # 系ごとの慣例の「前」と「上」
    "maya": ((0, 0, 1), (0, 1, 0)),
    "unity": ((0, 0, 1), (0, 1, 0)),
    "maya_m": ((0, 0, 1), (0, 1, 0)),
    "y_left_cm": ((0, 0, 1), (0, 1, 0)),
    "blender": ((0, 1, 0), (0, 0, 1)),
    "ue": ((1, 0, 0), (0, 0, 1)),
}


@pytest.mark.parametrize("name", list(FORWARD_UP))
def test_front_maps_to_canonical_plus_x_and_up_to_up(name):
    s = SPACES[name]
    forward, up = FORWARD_UP[name]
    cv = sp.converter(s, sp.CANONICAL)
    unit = sp.UNIT_TO_CM[s.unit]
    assert close(cv.position([f * 2 for f in forward]), (2 * unit, 0, 0))
    assert close(cv.position([u * 3 for u in up]), (0, 0, 3 * unit))


def test_point_in_front_of_y_up_character_facing_plus_z():
    # Maya / Unity の「+Z を向く Y-up キャラクター」の前 → 正準 +X、上 → +Z
    assert close(sp.convert_position([0, 0, 50], sp.MAYA, sp.CANONICAL), (50, 0, 0))
    assert close(sp.convert_position([0, 0, 0.5], sp.UNITY, sp.CANONICAL), (50, 0, 0))
    assert close(sp.convert_position([0, 170, 0], sp.MAYA, sp.CANONICAL), (0, 0, 170))
    assert close(sp.convert_position([0, 1.7, 0], sp.UNITY, sp.CANONICAL), (0, 0, 170))


def test_character_left_and_right():
    # Maya: 前 +Z のキャラクターの左は +X。UE: 前 +X のキャラクターの右は +Y（左 -Y）
    assert close(sp.convert_position([1, 0, 0], sp.MAYA, sp.UE), (0, -1, 0))
    # Unity: 前 +Z のキャラクターの右は +X（左手系）→ UE の右 +Y
    assert close(sp.convert_position([0.01, 0, 0], sp.UNITY, sp.UE), (0, 1, 0))


def test_unit_scaling():
    assert sp.convert_position([1, 0, 0], sp.SpaceSpec("m", "Z", "left"), sp.UE) == (100, 0, 0)
    assert close(sp.convert_position([100, 0, 0], sp.UE, sp.SpaceSpec("m", "Z", "left")), (1, 0, 0))
    assert close(sp.convert_position([10, 0, 0], sp.SpaceSpec("mm", "Z", "left"), sp.UE), (1, 0, 0))
    assert close(sp.convert_position([1, 0, 0], sp.SpaceSpec("in", "Z", "left"), sp.UE), (2.54, 0, 0))
    # 方向は単位の影響を受けない
    assert sp.convert_direction([0, 0, 1], sp.UNITY, sp.UE) == (1, 0, 0)


def test_matrices_are_signed_permutations_with_expected_determinant():
    for name, s in SPACES.items():
        m = sp.to_canonical_matrix(s)
        for row in m:
            assert sorted(abs(c) for c in row) == [0, 0, 1], name
        det = sp._det(m)  # noqa: SLF001
        flips = (s.handedness == "right")  # 正準は左手。右手系 → 左手系は鏡映
        assert det == (-1 if flips else 1), name


def test_space_of_accepts_names_meta_and_dicts():
    assert sp.space_of("Maya") == sp.MAYA
    assert sp.space_of(Meta("cm", "Y", "right")) == sp.MAYA
    assert sp.space_of({"unit": "m", "upAxis": "Y", "handedness": "left"}) == sp.UNITY
    for bad in ("nowhere", {"unit": "furlong", "upAxis": "Y", "handedness": "left"}, {"unit": "cm", "upAxis": "X", "handedness": "left"},
                {"unit": "cm", "upAxis": "Y", "handedness": "up"}):
        with pytest.raises(ValueError):
            sp.space_of(bad)
    with pytest.raises(TypeError):
        sp.space_of(3)  # type: ignore[arg-type]


# --- 往復（A → 正準 → A は恒等）---


@pytest.mark.parametrize("a", list(SPACES))
def test_round_trip_through_canonical(a):
    rng = random.Random(hash(a) & 0xFFFF)
    s = SPACES[a]
    to_c, from_c = sp.converter(s, sp.CANONICAL), sp.converter(sp.CANONICAL, s)
    for _ in range(100):
        p = rv(rng)
        assert close(from_c.position(to_c.position(p)), p)
        d = rv(rng, 1)
        assert close(from_c.direction(to_c.direction(d)), d)
        q = rq(rng)
        assert q_equiv(from_c.quaternion(to_c.quaternion(q)), q)
        sc = [rng.uniform(0.5, 2) for _ in range(3)]
        assert close(from_c.scale_vector(to_c.scale_vector(sc)), sc)
        b = BoneOffset(tuple(rv(rng, 5)), tuple(q), tuple(sc))
        b2 = from_c.bone_offset(to_c.bone_offset(b))
        assert close(b2.t, b.t) and q_equiv(b2.r, b.r) and close(b2.s, b.s)


@pytest.mark.parametrize("a,b", PAIRS)
def test_direct_conversion_equals_via_canonical(a, b):
    rng = random.Random(3)
    sa, sb = SPACES[a], SPACES[b]
    direct = sp.converter(sa, sb)
    to_c, from_c = sp.converter(sa, sp.CANONICAL), sp.converter(sp.CANONICAL, sb)
    for _ in range(20):
        p, q = rv(rng), rq(rng)
        assert close(direct.position(p), from_c.position(to_c.position(p)))
        assert q_equiv(direct.quaternion(q), from_c.quaternion(to_c.quaternion(q)))


@pytest.mark.parametrize("a,b", PAIRS)
def test_rotation_and_position_conversions_are_consistent(a, b):
    """回転してから変換 == 変換してから回転（位置と回転の変換が整合している）。"""
    rng = random.Random(11)
    cv = sp.converter(SPACES[a], SPACES[b])
    for _ in range(30):
        q, v = rq(rng), rv(rng, 10)
        left = cv.direction(sp.rotate_vector(q, v))
        right = sp.rotate_vector(cv.quaternion(q), cv.direction(v))
        assert close(left, right)


def test_quaternion_composition_commutes_with_conversion():
    rng = random.Random(5)
    cv = sp.converter(sp.MAYA, sp.UE)
    for _ in range(30):
        a, b = rq(rng), rq(rng)
        left = cv.quaternion(sp.quat_multiply(a, b))
        right = sp.quat_multiply(cv.quaternion(a), cv.quaternion(b))
        assert q_equiv(left, right)


# --- 手系の反転 ---


def test_rotation_about_up_flips_sign_between_handedness():
    # 右手系（Maya）で Y 軸まわり +θ は、左手系の正準で Z 軸まわり -θ
    theta = 37.0
    q_maya = sp.quat_from_axis_angle((0, 1, 0), theta)
    q_ue = sp.convert_quaternion(q_maya, sp.MAYA, sp.UE)
    expected = sp.quat_from_axis_angle((0, 0, 1), -theta)
    assert q_equiv(q_ue, expected)
    # 向きを位置で確かめる: 前 (Maya +Z) を +90° 回すと Maya +X（キャラクターの左）。正準では前 +X が左 -Y へ向く
    qm = sp.quat_from_axis_angle((0, 1, 0), 90)
    fwd_maya = sp.rotate_vector(qm, (0, 0, 1))
    assert close(fwd_maya, (1, 0, 0))
    fwd_ue = sp.rotate_vector(sp.convert_quaternion(qm, sp.MAYA, sp.UE), (1, 0, 0))
    assert close(fwd_ue, (0, -1, 0))
    assert close(fwd_ue, sp.convert_position(fwd_maya, sp.MAYA, sp.UE))


def test_rotation_about_up_keeps_sign_between_same_handedness():
    # Unity（左手）→ UE（左手）: Y 軸まわり +θ → Z 軸まわり +θ
    q = sp.convert_quaternion(sp.quat_from_axis_angle((0, 1, 0), 37.0), sp.UNITY, sp.UE)
    assert q_equiv(q, sp.quat_from_axis_angle((0, 0, 1), 37.0))
    # Unity ⇔ Maya は X 反転（鏡映）なので符号が逆
    q2 = sp.convert_quaternion(sp.quat_from_axis_angle((0, 1, 0), 37.0), sp.UNITY, sp.MAYA)
    assert q_equiv(q2, sp.quat_from_axis_angle((0, 1, 0), -37.0))


def test_w_is_preserved_and_result_stays_unit():
    rng = random.Random(2)
    for a, b in PAIRS:
        q = rq(rng)
        out = sp.convert_quaternion(q, SPACES[a], SPACES[b])
        assert out[3] == q[3]
        assert math.isclose(sum(c * c for c in out), 1.0, abs_tol=1e-12)


# --- forwardAxis / mirror ---


@pytest.mark.parametrize(
    "src,axis,expected",
    [
        (sp.MAYA, "+Z", "+X"), (sp.MAYA, "-Z", "-X"), (sp.MAYA, "+X", "-Y"), (sp.MAYA, "-X", "+Y"),
        (sp.UNITY, "+Z", "+X"), (sp.UNITY, "-Z", "-X"), (sp.UNITY, "+X", "+Y"), (sp.UNITY, "-X", "-Y"),
        (sp.UE, "+X", "+X"), (sp.UE, "-Y", "-Y"),
    ],
)
def test_forward_axis_to_canonical(src, axis, expected):
    assert sp.forward_axis_to_canonical(axis, src) == expected


def test_forward_axis_round_trip():
    for name in ("maya", "unity", "ue", "blender"):
        s = SPACES[name]
        for axis in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            if axis[1] == s.up_axis:
                continue
            there = sp.convert_forward_axis(axis, s, sp.CANONICAL)
            assert there in ("+X", "-X", "+Y", "-Y")
            assert sp.convert_forward_axis(there, sp.CANONICAL, s) == axis


def test_forward_axis_rejects_up_axis_and_garbage():
    with pytest.raises(ValueError):
        sp.convert_forward_axis("+Y", sp.MAYA, sp.UE)  # Maya の上軸は前方向にできない
    with pytest.raises(ValueError):
        sp.convert_forward_axis("-Z", sp.UE, sp.MAYA)
    with pytest.raises(ValueError):
        sp.convert_forward_axis("Z", sp.MAYA, sp.UE)


def test_forward_axis_to_canonical_feeds_yaw_offset():
    from tdrive_facial.core import evaluate as ev

    for axis in ("+Z", "-Z", "+X", "-X"):
        canon = sp.forward_axis_to_canonical(axis, sp.MAYA)
        ev.forward_axis_yaw_offset_deg(canon)  # ValueError にならない


def test_mirror_axis_mapping():
    assert sp.convert_mirror_axis("X", sp.MAYA, sp.UE) == "Y"
    assert sp.convert_mirror_axis("Y", sp.UE, sp.MAYA) == "X"
    assert sp.convert_mirror_axis("Y", sp.MAYA, sp.UE) == "Z"
    assert sp.convert_mirror_axis("Z", sp.MAYA, sp.UE) == "X"
    assert sp.convert_mirror_axis("X", sp.UNITY, sp.UE) == "Y"
    for a, b in PAIRS:
        for axis in "XYZ":
            there = sp.convert_mirror_axis(axis, SPACES[a], SPACES[b])
            assert sp.convert_mirror_axis(there, SPACES[b], SPACES[a]) == axis
    with pytest.raises(ValueError):
        sp.convert_mirror_axis("W", sp.MAYA, sp.UE)


# --- BoneOffset ---


def test_bone_offset_semantics_scale_rotation_translation():
    b = BoneOffset((0.5, 1.0, -2.0), tuple(sp.quat_from_axis_angle((0, 1, 0), 10)), (1.0, 1.1, 1.2), extra={"k": 1})
    out = sp.convert_bone_offset(b, sp.MAYA, sp.UE)
    assert close(out.t, (-2.0, -0.5, 1.0))
    assert close(out.s, (1.2, 1.0, 1.1))  # 成分の入れ替えのみ（符号なし）
    assert q_equiv(out.r, sp.quat_from_axis_angle((0, 0, 1), -10))
    assert out.extra == {"k": 1} and out.extra is not b.extra
    # 変換前後で「S → R → T を位置に当てた結果」が一致する
    p = (1.0, 2.0, 3.0)

    def apply(bone, point):
        scaled = [c * s for c, s in zip(point, bone.s)]
        rotated = sp.rotate_vector(bone.r, scaled)
        return [c + t for c, t in zip(rotated, bone.t)]

    expect = sp.convert_position(apply(b, p), sp.MAYA, sp.UE)
    got = apply(out, sp.convert_position(p, sp.MAYA, sp.UE))
    assert close(expect, got)


def test_bone_offset_unit_conversion_only_scales_translation():
    b = BoneOffset((0.01, 0.0, 0.0), (0, 0, 0, 1), (1, 1, 1))
    out = sp.convert_bone_offset(b, sp.UNITY, sp.UE)
    assert close(out.t, (0.0, 1.0, 0.0))
    assert out.r == (0.0, 0.0, 0.0, 1.0)
    assert out.s == (1, 1, 1)


# --- ドキュメントの変換 ---


def _tree_close(a, b, tol=1e-9):
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for k in a:
            _tree_close(a[k], b[k], tol)
    elif isinstance(a, list):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            _tree_close(x, y, tol)
    elif isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, abs=tol)
    else:
        assert a == b


def _maya_doc():
    d = Document()
    d.meta = Meta("cm", "Y", "right", "Maya2026 T-Drive")
    d.grid.forward_axis = "+Z"
    d.grid.center_offset = (0.0, 1.5, 0.5)
    d.mirror.bone_axis = "X"
    d.layers[0].points[(1, 4)] = GridPoint(
        1, 4, True,
        SourcePose({"bs.smile": 0.4}, {"bone_eye_L": BoneOffset((0, 0.05, 0.02), tuple(sp.quat_from_axis_angle((0, 1, 0), 12)), (1, 1.05, 1))}),
    )
    d.layers.append(Layer("Joy", "emo", True))
    d.layers[1].points[(0, 0)] = GridPoint(0, 0, False, SourcePose({}, {"b": BoneOffset((1, 2, 3))}))
    d.extra = {"future": {"x": 1}}
    return d


def test_convert_document_to_ue_and_back():
    d = _maya_doc()
    before = copy.deepcopy(io.to_dict(d))
    ue = sp.convert_document(d, sp.UE)
    assert io.to_dict(d) == before  # 元は変えない
    assert (ue.meta.unit, ue.meta.up_axis, ue.meta.handedness, ue.meta.source) == ("cm", "Z", "left", "Maya2026 T-Drive")
    assert ue.grid.forward_axis == "+X"
    assert close(ue.grid.center_offset, (0.5, 0.0, 1.5))
    assert ue.mirror.bone_axis == "Y"
    assert ue.layers[0].points[(1, 4)].pose.curves == {"bs.smile": 0.4}
    assert close(ue.layers[1].points[(0, 0)].pose.bones["b"].t, (3, -1, 2))
    assert ue.extra == {"future": {"x": 1}}
    back = sp.convert_document(ue, sp.MAYA)
    _tree_close(io.to_dict(back), before)


def test_convert_document_same_space_is_a_copy():
    d = _maya_doc()
    same = sp.convert_document(d, sp.MAYA)
    _tree_close(io.to_dict(same), io.to_dict(d))
    assert same is not d and same.layers[0] is not d.layers[0]


def test_convert_document_unity_units():
    d = _maya_doc()
    u = sp.convert_document(d, sp.UNITY)
    assert u.meta.unit == "m"
    assert u.grid.forward_axis == "+Z" and u.mirror.bone_axis == "X"
    # Maya (x, y, z) cm → Unity (-x, y, z) m
    assert close(u.grid.center_offset, (0.0, 0.015, 0.005), 1e-12)
    b = u.layers[1].points[(0, 0)].pose.bones["b"]
    assert close(b.t, (-0.01, 0.02, 0.03), 1e-12)


def test_convert_pose_document():
    p = PoseDocument(meta=Meta("cm", "Y", "right"), pose=SourcePose({"a": 1.0}, {"b": BoneOffset((1, 2, 3))}), extra={"k": 1})
    out = sp.convert_pose_document(p, sp.UE)
    assert out.meta.up_axis == "Z" and out.meta.handedness == "left"
    assert close(out.pose.bones["b"].t, (3, -1, 2))
    assert out.pose.curves == {"a": 1.0} and out.extra == {"k": 1}
    assert p.meta.up_axis == "Y"


def test_ue_written_fixture_converts_to_maya_and_back():
    from pathlib import Path

    fx = Path(__file__).parent / "fixtures" / "ue_full_asset.fcpose.json"
    ue = io.load_document(fx)
    maya = sp.convert_document(ue, sp.MAYA)
    assert maya.meta.up_axis == "Y" and maya.grid.forward_axis == "+Z" and maya.mirror.bone_axis == "X"
    back = sp.convert_document(maya, sp.UE)
    _tree_close(io.to_dict(back), io.to_dict(ue), 1e-9)


# --- 視点の角度（各環境の座標から）---


def test_view_angles_agree_across_environments():
    """同じ物理的な配置を Maya / Unity / UE の座標で書いても (Yaw, Pitch) が一致する。"""
    rng = random.Random(4)
    for _ in range(40):
        # UE（正準）で配置を決める
        head = [rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(100, 180)]
        viewer = [h + rng.uniform(-300, 300) for h in head]
        yaw_rot = rng.uniform(-180, 180)
        axis = rng.choice(["+X", "-X", "+Y", "-Y"])
        offset = rv(rng, 5)
        q_ue = sp.quat_from_axis_angle((0, 0, 1), yaw_rot)
        expected = sp.compute_view_angles_in_space(sp.UE, head, q_ue, axis, viewer, offset)
        for target in (sp.MAYA, sp.UNITY, SPACES["blender"]):
            cv = sp.converter(sp.UE, target)
            got = sp.compute_view_angles_in_space(
                target, cv.position(head), cv.quaternion(q_ue), cv.forward_axis(axis), cv.position(viewer), cv.position(offset)
            )
            assert got[0] == pytest.approx(expected[0], abs=1e-7) or abs(abs(got[0] - expected[0]) - 360) < 1e-7
            assert got[1] == pytest.approx(expected[1], abs=1e-7)


def test_view_angles_in_space_matches_bone_yaw_plus_axis_offset():
    # UE 版: WorldForwardYaw = ボーンのワールド Yaw + forwardAxis のオフセット。直立なら回転から求めた方向と同じ
    from tdrive_facial.core import evaluate as ev

    rng = random.Random(9)
    for _ in range(50):
        bone_yaw = rng.uniform(-180, 180)
        axis = rng.choice(["+X", "-X", "+Y", "-Y"])
        head = rv(rng, 50)
        viewer = rv(rng, 300)
        q = sp.quat_from_axis_angle((0, 0, 1), bone_yaw)
        a = sp.compute_view_angles_in_space(sp.UE, head, q, axis, viewer)
        b = ev.compute_view_angles(head, bone_yaw + ev.forward_axis_yaw_offset_deg(axis), viewer)
        assert ev.normalize_axis(a[0] - b[0]) == pytest.approx(0.0, abs=1e-7)
        assert a[1] == pytest.approx(b[1], abs=1e-9)


def test_view_angles_in_space_handles_no_rotation_and_center_offset():
    # 無回転・前 +Z の Maya キャラクターを前から: Yaw 0 / Pitch 0。centerOffset を上へ 10 ずらすと Pitch は下がる
    y0, p0 = sp.compute_view_angles_in_space(sp.MAYA, [0, 150, 0], None, "+Z", [0, 150, 100])
    assert (round(y0, 9), round(p0, 9)) == (0, 0)
    _, p1 = sp.compute_view_angles_in_space(sp.MAYA, [0, 150, 0], None, "+Z", [0, 150, 100], (0, 10, 0))
    assert p1 == pytest.approx(-math.degrees(math.atan2(10, 100)))
