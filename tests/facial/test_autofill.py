"""tdrive_facial.core.autofill（自動生成・格子サイズ変更・コピー）のテスト。
UE 版 `FacialCorrectionAsset.cpp` / `FacialCorrectionCopier.cpp` の写しが対象。共通のテストデータは conformance/autofill.json
（形式は conformance/README_autofill.md）。ここは手計算の小さいケースと性質（べき等・対称・キー不変）を確かめる。"""

import copy
import json
import math
from pathlib import Path

import pytest

from tdrive_facial.core import autofill as af
from tdrive_facial.core import space as sp
from tdrive_facial.core.evaluate import point_angles
from tdrive_facial.core.model import (
    Autogen,
    BoneOffset,
    Document,
    Grid,
    GridPoint,
    Layer,
    Meta,
    Mirror,
    SourcePose,
    MAX_LAYERS,
)

CONF = Path(__file__).parent / "conformance"
TOL = 1e-4


# --- 組み立ての補助 ---


def make_doc(cols=5, rows=3, yaw=90.0, pitch=45.0, mirror=True, mode="IDW", power=2.0, axis="Y", exclude=()):
    d = Document()
    d.grid = Grid(yaw_range=yaw, pitch_range=pitch, cols=cols, rows=rows)
    d.mirror = Mirror(enabled=mirror, bone_axis=axis, exclude=list(exclude))
    d.autogen = Autogen(mode=mode, idw_power=power)
    return d


def put(doc, row, col, curves=None, bones=None, key=True, layer=0):
    p = GridPoint(row, col, key, SourcePose(dict(curves or {}), dict(bones or {})))
    doc.layers[layer].points[(row, col)] = p
    return p


def bone(t=(0.0, 0.0, 0.0), r=(0.0, 0.0, 0.0, 1.0), s=(1.0, 1.0, 1.0)):
    return BoneOffset(t=tuple(t), r=tuple(r), s=tuple(s))


def qz(deg):
    h = math.radians(deg) / 2
    return (0.0, 0.0, math.sin(h), math.cos(h))


def snapshot(doc):
    return {
        li: {k: (p.is_key, copy.deepcopy(p.pose)) for k, p in layer.points.items()}
        for li, layer in enumerate(doc.layers)
    }


def pose_close(a, b, tol=TOL):
    assert set(a.curves) == set(b.curves), (a.curves, b.curves)
    for k in a.curves:
        assert a.curves[k] == pytest.approx(b.curves[k], abs=tol), k
    assert set(a.bones) == set(b.bones), (list(a.bones), list(b.bones))
    for k in a.bones:
        for x, y in zip(a.bones[k].t, b.bones[k].t):
            assert x == pytest.approx(y, abs=tol), k
        for x, y in zip(a.bones[k].r, b.bones[k].r):
            assert x == pytest.approx(y, abs=tol), k
        for x, y in zip(a.bones[k].s, b.bones[k].s):
            assert x == pytest.approx(y, abs=tol), k


# --- 名前・ボーンの鏡映 ---


def test_mirror_name_swaps_suffix_and_leaves_others():
    m = Mirror()
    assert af.mirror_name("Smile_L", m) == "Smile_R"
    assert af.mirror_name("Smile_R", m) == "Smile_L"
    assert af.mirror_name("Smile", m) == "Smile"
    assert af.mirror_name("Smile_L_x", m) == "Smile_L_x"  # 末尾だけ
    assert af.mirror_name("_L", m) == "_R"
    assert af.mirror_name("smile_l", m) == "smile_l"  # 大文字小文字を区別
    assert af.mirror_name("Smile_L", Mirror(suffix_l="", suffix_r="_R")) == "Smile_L"


def test_exclude_is_substring_match_and_ignores_empty_pattern():
    m = Mirror(exclude=["Tongue", ""])
    assert af.is_mirror_excluded("TongueOut", m)
    assert af.is_mirror_excluded("A_Tongue_L", m)  # 部分一致（UE は Contains）
    assert not af.is_mirror_excluded("tongue", m)
    assert not af.is_mirror_excluded("Smile_L", m)
    assert not af.is_mirror_excluded("Smile_L", Mirror(exclude=[""]))


@pytest.mark.parametrize(
    "axis,t_out,r_out",
    [
        ("X", (-1.0, 2.0, 3.0), (0.1, -0.2, -0.3)),
        ("Y", (1.0, -2.0, 3.0), (-0.1, 0.2, -0.3)),
        ("Z", (1.0, 2.0, -3.0), (-0.1, -0.2, 0.3)),
    ],
)
def test_mirror_bone_offset_reflects_across_axis(axis, t_out, r_out):
    q = af._quat_normalize((0.1, 0.2, 0.3, 0.9))
    out = af.mirror_bone_offset(bone(t=(1, 2, 3), r=q, s=(2, 2, 2)), axis)
    assert out.t == pytest.approx(t_out)
    # 回転: 軸まわりの成分を残し、他の 2 成分の符号を反転（r_out は符号の並びを (±0.1, ±0.2, ±0.3) で表している）
    ratio = [o / i for o, i in zip(out.r[:3], q[:3])]
    want = [r / x for r, x in zip(r_out, (0.1, 0.2, 0.3))]
    assert ratio == pytest.approx(want)
    assert out.r[3] == pytest.approx(q[3])
    assert out.s == (1.0, 1.0, 1.0)  # スケールは 1 に戻る（UE 準拠）
    assert sum(c * c for c in out.r) == pytest.approx(1.0)


def test_mirror_bone_offset_twice_is_identity():
    b = bone(t=(1, -2, 0.5), r=af._quat_normalize((0.3, -0.1, 0.2, 0.8)))
    for axis in "XYZ":
        back = af.mirror_bone_offset(af.mirror_bone_offset(b, axis), axis)
        assert back.t == pytest.approx(b.t)
        assert back.r == pytest.approx(b.r)


def test_mirror_bone_offset_unknown_axis():
    with pytest.raises(ValueError):
        af.mirror_bone_offset(bone(), "W")


def test_mirror_pose_swaps_names_and_skips_excluded():
    m = Mirror(exclude=["Brow"], bone_axis="Y")
    pose = SourcePose(
        {"Smile_L": 1.0, "Brow_L": 0.5, "Jaw": 0.2},
        {"Eye_L": bone(t=(0, 1, 0)), "BrowBone_R": bone(t=(0, 1, 0))},
    )
    out = af.mirror_pose(pose, m)
    assert out.curves == {"Smile_R": 1.0, "Jaw": 0.2}
    assert list(out.bones) == ["Eye_R"]
    assert out.bones["Eye_R"].t == (0, -1, 0)
    assert pose.curves["Smile_L"] == 1.0  # 入力は変えない


def test_mirror_pose_ignores_enabled_flag():
    out = af.mirror_pose(SourcePose({"A_L": 1.0}), Mirror(enabled=False))
    assert out.curves == {"A_R": 1.0}


# --- 補間・自動生成（手計算）---


def test_idw_two_keys_hand_computed():
    # 5 列 1 行、yaw ±90 → 角度 -90, -45, 0, 45, 90。col1(-45) の距離は col0 が 45/90=0.5、col3 が 90/90=1.0
    # べき乗 2 → 重み 4 と 1 → alpha 0.8 / 0.2 → A = 0.8*1.0 + 0.2*0.4 = 0.88
    d = make_doc(cols=5, rows=1, mirror=False)
    put(d, 0, 0, {"A": 1.0})
    put(d, 0, 3, {"A": 0.4})
    s = af.generate_from_keys(d, 0)
    assert d.layers[0].points[(0, 1)].pose.curves["A"] == pytest.approx(0.88)
    assert d.layers[0].points[(0, 1)].is_key is False
    assert (s.keys, s.mirror_keys, s.generated, s.cleared) == (2, 0, 3, 0)


def test_idw_distance_is_normalised_by_range():
    # 1 列 3 行、pitch ±45 → 角度 -45, 0, 45（yaw は 0）。row2(45) からの距離: row0 = 90/45 = 2、row1 = 45/45 = 1
    # 重み 1/4 と 1 → alpha 0.2 / 0.8
    d = make_doc(cols=1, rows=3, mirror=False)
    put(d, 0, 0, {"A": 1.0})
    put(d, 1, 0, {"B": 1.0})
    af.generate_from_keys(d, 0)
    pose = d.layers[0].points[(2, 0)].pose
    assert pose.curves["A"] == pytest.approx(0.2)
    assert pose.curves["B"] == pytest.approx(0.8)


def test_idw_power_changes_weight():
    for power, expect_a in [(1.0, 2 / 3), (2.0, 0.8), (4.0, 16 / 17)]:
        d = make_doc(cols=5, rows=1, mirror=False, power=power)
        put(d, 0, 0, {"A": 1.0})
        put(d, 0, 3, {"B": 1.0})
        af.generate_from_keys(d, 0)
        # col1: 距離 0.5 と 1.0 → w0 = 0.5^-p, w3 = 1 → alpha0 = 2^p / (2^p + 1)
        a = 2**power / (2**power + 1)
        assert d.layers[0].points[(0, 1)].pose.curves["A"] == pytest.approx(a)
        assert a == pytest.approx(expect_a)


def test_nearest_key_copies_nearest_and_first_wins_on_tie():
    d = make_doc(cols=3, rows=1, mirror=False, mode="NearestKey")
    put(d, 0, 0, {"A": 1.0})
    put(d, 0, 2, {"B": 1.0})
    # 中央（0°）は両方から同じ距離 → 先に集めた col0 のキー
    af.generate_from_keys(d, 0)
    assert d.layers[0].points[(0, 1)].pose.curves == {"A": 1.0}


def test_nearest_key_uses_raw_degrees_not_normalised():
    # 3x3、yaw ±90 / pitch ±10。点 (row0, col1) は角度 (0, -10)
    # キー 1 = (-90, -10): 距離 90°。キー 2 = (0, 10): 距離 20°（IDW の正規化なら pitch 側が大きく効く）
    d = make_doc(cols=3, rows=3, yaw=90, pitch=10, mirror=False, mode="NearestKey")
    put(d, 0, 0, {"A": 1.0})
    put(d, 2, 1, {"B": 1.0})
    af.generate_from_keys(d, 0)
    assert d.layers[0].points[(0, 1)].pose.curves == {"B": 1.0}


def test_exact_hit_copies_key_including_scale():
    d = make_doc(cols=3, rows=1, mirror=False)
    put(d, 0, 0, bones={"B": bone(t=(1, 0, 0), s=(2, 2, 2))})
    # ミラー無効: col1 は IDW。スケールは補間されず 1
    put(d, 0, 2, bones={"B": bone(t=(1, 0, 0), s=(3, 3, 3))})
    af.generate_from_keys(d, 0)
    assert d.layers[0].points[(0, 1)].pose.bones["B"].s == (1.0, 1.0, 1.0)
    # 完全一致（仮想キーが格子の点に重なる）→ スケールもそのまま（ここでは仮想キーなので 1。実キーの interpolate で確認）
    pose = af.interpolate_pose_at_angles(d, d.layers[0], -90.0, 0.0)
    assert pose.bones["B"].s == (2, 2, 2)


def test_scale_is_never_interpolated():
    d = make_doc(cols=3, rows=1, mirror=False)
    put(d, 0, 0, bones={"B": bone(t=(1, 0, 0), s=(2, 2, 2))})
    put(d, 0, 2, bones={"B": bone(t=(1, 0, 0), s=(4, 4, 4))})
    pose = af.interpolate_pose_at_angles(d, 0, 0.0, 0.0)
    assert pose.bones["B"].s == (1.0, 1.0, 1.0)
    assert pose.bones["B"].t == pytest.approx((1.0, 0.0, 0.0))


def test_mirror_virtual_key_makes_exact_copy_at_mirrored_point():
    d = make_doc()
    put(d, 1, 3, {"Smile_L": 1.0, "Smile_R": 0.2, "Jaw": 0.5})
    s = af.generate_from_keys(d, 0)
    assert s.mirror_keys == 1
    # col1 = -45° は仮想キーと一致 → 完全一致でコピー
    assert d.layers[0].points[(1, 1)].pose.curves == {"Smile_R": 1.0, "Smile_L": 0.2, "Jaw": 0.5}
    assert d.layers[0].points[(1, 1)].is_key is False


def test_real_key_within_one_degree_suppresses_virtual_key():
    d = make_doc()
    put(d, 1, 3, {"A_L": 1.0})
    put(d, 1, 1, {"A_L": 0.3})
    s = af.generate_from_keys(d, 0)
    assert s.mirror_keys == 0
    d2 = make_doc(cols=181, rows=1, yaw=90, pitch=45)  # 1 列 = 1° 刻み
    put(d2, 0, 135, {"A_L": 1.0})  # +45°
    put(d2, 0, 45, {"A_L": 0.3})  # -45°: ちょうど反転位置
    assert af.generate_from_keys(d2, 0).mirror_keys == 0
    d3 = make_doc(cols=181, rows=1, yaw=90, pitch=45)
    put(d3, 0, 135, {"A_L": 1.0})
    put(d3, 0, 46, {"A_L": 0.3})  # -44°: 反転位置から 1° ちょうど（< 1° ではない）→ 作る。-44° のキーの反転（+44°）も +45° から 1°
    assert af.generate_from_keys(d3, 0).mirror_keys == 2
    d4 = make_doc(cols=361, rows=1, yaw=90, pitch=45)  # 0.5° 刻み
    put(d4, 0, 270, {"A_L": 1.0})  # +45°
    put(d4, 0, 91, {"A_L": 0.3})  # -44.5°: 反転位置から 0.5° → どちらの仮想キーも作らない
    assert af.generate_from_keys(d4, 0).mirror_keys == 0


def test_virtual_key_occupancy_also_checks_earlier_virtual_keys():
    # 実キー 2 つが互いの反転位置 → どちらからも仮想キーを作らない
    d = make_doc()
    put(d, 0, 0, {"A": 1.0})
    put(d, 0, 4, {"A": 0.5})
    assert af.generate_from_keys(d, 0).mirror_keys == 0


def test_front_column_keys_are_not_mirrored():
    d = make_doc()
    put(d, 1, 2, {"A_L": 1.0})
    assert af.generate_from_keys(d, 0).mirror_keys == 0
    # |yaw| <= 0.1° は正面扱い。それより大きくても反転位置が自分自身から 1° 未満（|yaw| < 0.5°）なら
    # 「自分が占有している」ので作らない（UE は増えていく配列に自分も含めて判定する）。|yaw| >= 0.5° で作る
    for yaw, expect in [(0.1, 0), (0.4, 0), (0.6, 1)]:
        d2 = make_doc(cols=3, rows=1, yaw=yaw)
        put(d2, 0, 2, {"A_L": 1.0})
        assert af.generate_from_keys(d2, 0).mirror_keys == expect, yaw


def test_exclude_patterns_skip_virtual_key_names():
    d = make_doc(exclude=["Tongue"])
    put(d, 1, 3, {"Smile_L": 1.0, "TongueOut": 1.0})
    af.generate_from_keys(d, 0)
    pose = d.layers[0].points[(1, 1)].pose
    assert "Smile_R" in pose.curves and "TongueOut" not in pose.curves


def test_threshold_drops_small_curves_and_bones():
    d = make_doc(cols=3, rows=1, mirror=False)
    put(d, 0, 1, {"Tiny": 0.001, "Big": 1.0}, {"T": bone(t=(0.001, 0, 0)), "K": bone(t=(0.002, 0, 0))})
    af.generate_from_keys(d, 0)
    pose = d.layers[0].points[(0, 0)].pose
    assert set(pose.curves) == {"Big"}  # 0.001 は「1e-3 以下」なので捨てる
    assert set(pose.bones) == {"K"}


def test_bone_with_rotation_only_is_kept_and_identity_dropped():
    d = make_doc(cols=3, rows=1, mirror=False)
    put(d, 0, 1, bones={"R": bone(r=qz(10)), "I": bone(r=(0, 0, 0, 1))})
    af.generate_from_keys(d, 0)
    assert set(d.layers[0].points[(0, 0)].pose.bones) == {"R"}


def test_bone_rotation_hemisphere_alignment():
    d = make_doc(cols=3, rows=1, mirror=False)
    q = qz(40)
    put(d, 0, 0, bones={"N": bone(r=q)})
    put(d, 0, 2, bones={"N": bone(r=tuple(-v for v in q))})
    pose = af.interpolate_pose_at_angles(d, 0, 0.0, 0.0)
    assert pose.bones["N"].r == pytest.approx(q, abs=1e-9)  # 打ち消し合わずに同じ回転になる


def test_bone_rotation_weighted_and_normalised():
    # 距離 0.5 / 1.0（べき乗 2）→ alpha 0.8 / 0.2。回転の和 0.8*q1 + 0.2*q2 を正規化
    d = make_doc(cols=5, rows=1, mirror=False)
    q1, q2 = qz(0), qz(60)
    put(d, 0, 0, bones={"N": bone(r=q1)})
    put(d, 0, 3, bones={"N": bone(r=q2)})
    af.generate_from_keys(d, 0)
    got = d.layers[0].points[(0, 1)].pose.bones["N"].r
    raw = [0.8 * a + 0.2 * b for a, b in zip(q1, q2)]
    n = math.sqrt(sum(v * v for v in raw))
    assert got == pytest.approx([v / n for v in raw], abs=1e-9)


# --- 性質 ---


def _rich_doc():
    d = make_doc()
    d.mirror.exclude = ["Tongue"]
    put(d, 1, 3, {"Smile_L": 1.0, "Brow_Up": 0.5, "TongueOut": 0.4},
        {"Eye_L": bone(t=(0, 2, 1), r=qz(30)), "Head": bone(t=(0.5, 0, 0))})
    put(d, 0, 2, {"Blink_L": 0.2, "Blink_R": 0.3})
    put(d, 2, 4, {"Smile_R": 0.7}, {"Eye_R": bone(t=(0, -1, 0), r=qz(-20))})
    return d


def test_keys_are_untouched():
    d = _rich_doc()
    before = {k: (p.is_key, copy.deepcopy(p.pose)) for k, p in d.layers[0].points.items()}
    af.generate_from_keys(d, 0)
    for k, (is_key, pose) in before.items():
        p = d.layers[0].points[k]
        assert p.is_key and is_key
        assert p.pose == pose
    assert all(not p.is_key for k, p in d.layers[0].points.items() if k not in before)


def test_generate_is_idempotent():
    d = _rich_doc()
    s1 = af.generate_from_keys(d, 0)
    snap1 = snapshot(d)
    s2 = af.generate_from_keys(d, 0)
    assert snapshot(d) == snap1
    assert s1 == s2


def test_generate_overwrites_stale_generated_points_and_removes_empty_ones():
    d = make_doc(cols=3, rows=1, mirror=False)
    put(d, 0, 1, {"Tiny": 0.0005})  # 全部しきい値以下 → 生成結果は空
    put(d, 0, 0, {"Stale": 1.0}, key=False)  # 古い自動生成
    stale_outside = GridPoint(5, 5, False, SourcePose({"Out": 1.0}))
    d.layers[0].points[(5, 5)] = stale_outside  # 格子の外の点は触らない
    s = af.generate_from_keys(d, 0)
    assert (0, 0) not in d.layers[0].points and (0, 2) not in d.layers[0].points
    assert s.generated == 0 and s.cleared == 1
    assert d.layers[0].points[(5, 5)] is stale_outside


def test_generate_without_keys_changes_nothing():
    d = make_doc()
    put(d, 0, 0, {"A": 1.0}, key=False)
    before = snapshot(d)
    s = af.generate_from_keys(d, 0)
    assert snapshot(d) == before
    assert s.layers == 0 and s.skipped_layers == ["Neutral"]


def test_generate_all_layers_and_single_layer():
    d = make_doc(mirror=False)
    d.layers.append(Layer(name="Happy"))
    put(d, 1, 2, {"A": 1.0}, layer=0)
    put(d, 1, 2, {"B": 1.0}, layer=1)
    s = af.generate_from_keys(d)
    assert s.layers == 2 and s.keys == 2 and s.generated == 28
    d2 = make_doc(mirror=False)
    d2.layers.append(Layer(name="Happy"))
    put(d2, 1, 2, {"A": 1.0}, layer=0)
    put(d2, 1, 2, {"B": 1.0}, layer=1)
    af.generate_from_keys(d2, 1)
    assert len(d2.layers[0].points) == 1 and len(d2.layers[1].points) == 15
    with pytest.raises(IndexError):
        af.generate_from_keys(d2, 5)


def test_unknown_autogen_mode_is_rejected():
    d = make_doc(mode="Bilinear")
    with pytest.raises(ValueError):
        af.generate_from_keys(d, 0)


def test_mirror_symmetry_of_generated_points():
    d2 = make_doc()
    put(d2, 1, 3, {"Smile_L": 1.0, "Brow_Up": 0.5}, {"Eye_L": bone(t=(0, 2, 1), r=qz(30))})
    put(d2, 0, 2, {"Blink": 0.2})  # 正面列のキーは自分自身がミラー（L/R の名前は付けない）
    put(d2, 2, 4, {"Smile_R": 0.7})
    af.generate_from_keys(d2, 0)
    p2 = d2.layers[0].points
    n = 0
    for (r, c), p in p2.items():
        mc = 4 - c
        if (r, mc) in p2 and c != mc and not p.is_key and not p2[(r, mc)].is_key:
            pose_close(p2[(r, mc)].pose, af.mirror_pose(p.pose, d2.mirror), tol=1e-9)
            n += 1
    assert n >= 4
    # 実キー (2,4) の反転位置 (2,0) は仮想キー = 実キーの鏡映と同じ
    pose_close(p2[(2, 0)].pose, af.mirror_pose(p2[(2, 4)].pose, d2.mirror), tol=1e-9)


# --- 座標系（文書の系のまま生成 == 正準へ変換して生成して戻す）---


def _space_doc(meta, forward, axis, unit_scale=1.0):
    d = make_doc(axis=axis)
    d.meta = meta
    d.grid.forward_axis = forward
    put(d, 1, 3, {"Smile_L": 1.0, "Brow_Up": 0.5},
        {"Eye_L": bone(t=(0.3 * unit_scale, 2.0 * unit_scale, 1.0 * unit_scale), r=af._quat_normalize((0.2, -0.1, 0.3, 0.9))),
         "Tiny": bone(t=(0.0005 * unit_scale, 0, 0)), "Head": bone(r=qz(15))})
    put(d, 0, 2, {"Blink_L": 0.2}, {"Head": bone(r=af._quat_normalize((0.1, 0.1, 0.0, -0.99)))})
    put(d, 2, 4, {"Smile_R": 0.7}, {"Eye_R": bone(t=(0.0, -1.0 * unit_scale, 0.5 * unit_scale), r=qz(-20))})
    return d


@pytest.mark.parametrize(
    "meta,forward,axis,scale",
    [
        (sp.MAYA, "+Z", "X", 1.0),
        (sp.UNITY, "+Z", "X", 0.01),
        (sp.UE, "+X", "Y", 1.0),
        (Meta(unit="cm", up_axis="Z", handedness="right"), "+X", "Y", 1.0),
    ],
    ids=["maya", "unity-m", "ue", "z-up-right"],
)
@pytest.mark.parametrize("mode", ["IDW", "NearestKey"])
def test_generate_in_document_space_equals_canonical_roundtrip(meta, forward, axis, scale, mode):
    d = _space_doc(Meta(unit=meta.unit, up_axis=meta.up_axis, handedness=meta.handedness), forward, axis, scale)
    d.autogen.mode = mode
    direct = copy.deepcopy(d)
    s_direct = af.generate_from_keys(direct, 0)

    canon = sp.convert_document(d, sp.CANONICAL)
    s_canon = af.generate_from_keys(canon, 0)
    back = sp.convert_document(canon, d.meta)

    assert s_direct == s_canon
    assert set(direct.layers[0].points) == set(back.layers[0].points)
    for k, p in direct.layers[0].points.items():
        q = back.layers[0].points[k]
        assert p.is_key == q.is_key
        pose_close(p.pose, q.pose, tol=1e-7)


def test_translation_threshold_scales_with_document_unit():
    # 5e-6 m = 5e-4 cm（捨てる）、5e-5 m = 5e-3 cm（残す）
    d = make_doc(cols=3, rows=1, mirror=False)
    d.meta = Meta(unit="m", up_axis="Y", handedness="left")
    put(d, 0, 1, bones={"Drop": bone(t=(5e-6, 0, 0)), "Keep": bone(t=(5e-5, 0, 0))})
    af.generate_from_keys(d, 0)
    assert set(d.layers[0].points[(0, 0)].pose.bones) == {"Keep"}


# --- interpolate_pose_at_angles ---


def test_interpolate_without_keys_is_empty_and_does_not_touch_doc():
    d = make_doc()
    assert af.interpolate_pose_at_angles(d, 0, 10, 5).is_empty()
    put(d, 1, 3, {"A": 1.0}, key=False)  # 非キーは入力にならない
    assert af.interpolate_pose_at_angles(d, d.layers[0], 10, 5).is_empty()
    assert len(d.layers[0].points) == 1


def test_interpolate_at_key_angle_returns_copy():
    d = make_doc()
    put(d, 1, 3, {"A": 1.0})
    pose = af.interpolate_pose_at_angles(d, 0, 45.0, 0.0)
    assert pose.curves == {"A": 1.0}
    pose.curves["A"] = 9.0
    assert d.layers[0].points[(1, 3)].pose.curves["A"] == 1.0


def test_interpolate_mirror_option():
    d = make_doc()
    put(d, 1, 3, {"A_L": 1.0})
    assert set(af.interpolate_pose_at_angles(d, 0, -45.0, 0.0).curves) == {"A_L"}
    assert af.interpolate_pose_at_angles(d, 0, -45.0, 0.0, mirror=True).curves == {"A_R": 1.0}
    d.mirror.enabled = False
    assert set(af.interpolate_pose_at_angles(d, 0, -45.0, 0.0, mirror=True).curves) == {"A_L"}


# --- resize_grid ---


def _angles(doc, row, col):
    g = doc.grid
    return point_angles(g.yaw_range, g.pitch_range, g.cols, g.rows, row, col)


def test_resize_keeps_keys_by_angle():
    d = make_doc(cols=5, rows=3, mirror=False)
    put(d, 1, 2, {"A": 1.0})  # (0, 0)
    put(d, 1, 3, {"B": 1.0})  # (45, 0)
    put(d, 0, 0, {"C": 1.0})  # (-90, -45)
    s = af.resize_grid(d, 9, 5)
    pts = d.layers[0].points
    assert pts[(2, 4)].is_key and pts[(2, 4)].pose.curves == {"A": 1.0}
    assert pts[(2, 6)].is_key and pts[(2, 6)].pose.curves == {"B": 1.0}
    assert pts[(0, 0)].is_key and pts[(0, 0)].pose.curves == {"C": 1.0}
    assert s.kept_keys == 3 and s.dropped_keys == []
    assert sum(1 for p in pts.values() if p.is_key) == 3
    assert (d.grid.cols, d.grid.rows) == (9, 5)
    # 角度が同じ
    for (r, c) in [(2, 4), (2, 6), (0, 0)]:
        assert all(math.isfinite(v) for v in _angles(d, r, c))


def test_resize_resamples_non_key_points_from_keys():
    d = make_doc(cols=3, rows=1, mirror=False)
    put(d, 0, 0, {"A": 1.0})
    put(d, 0, 2, {"A": 0.0, "B": 1.0})
    af.generate_from_keys(d, 0)
    s = af.resize_grid(d, 5, 1)
    pts = d.layers[0].points
    assert s.kept_keys == 2 and s.resampled == 3
    assert pts[(0, 0)].is_key and pts[(0, 4)].is_key
    assert not pts[(0, 2)].is_key
    assert pts[(0, 2)].pose.curves["A"] == pytest.approx(0.5)  # 中央は 2 キーの中間（距離が同じ）
    # 旧格子の非キーは引き継がない: 角度 -45° の値は旧 (0,1)（=0°）ではなく、キーからの補間
    expect = af.interpolate_pose_at_angles(d, 0, *_angles(d, 0, 1))
    pose_close(pts[(0, 1)].pose, expect)


def test_resize_drops_keys_that_fall_on_no_grid_point_and_reports_them():
    d = make_doc(cols=5, rows=3, mirror=False)
    put(d, 1, 1, {"A": 1.0})  # (-45, 0) → 3 列にすると (-90, 0), (0, 0), (90, 0): どれにも重ならない
    put(d, 1, 2, {"B": 1.0})  # (0, 0) → 残る
    s = af.resize_grid(d, 3, 3)
    assert s.kept_keys == 1
    assert len(s.dropped_keys) == 1
    dk = s.dropped_keys[0]
    assert (dk.layer, dk.row, dk.col, dk.yaw, dk.pitch) == ("Neutral", 1, 1, -45.0, 0.0)
    assert d.layers[0].points[(1, 1)].is_key and d.layers[0].points[(1, 1)].pose.curves == {"B": 1.0}
    assert sum(1 for p in d.layers[0].points.values() if p.is_key) == 1


def test_resize_with_range_change_moves_angles():
    d = make_doc(cols=5, rows=3, mirror=False)
    put(d, 1, 2, {"A": 1.0})  # (0, 0)
    put(d, 1, 3, {"B": 1.0})  # (45, 0)
    s = af.resize_grid(d, 5, 3, yaw_range=60.0)
    assert d.grid.yaw_range == 60.0 and d.grid.pitch_range == 45.0
    assert [(k.row, k.col) for k in s.dropped_keys] == [(1, 3)]  # 新しい角度は -60, -30, 0, 30, 60
    assert d.layers[0].points[(1, 2)].is_key
    s2 = af.resize_grid(d, 5, 3, yaw_range=90.0, pitch_range=45.0)
    assert s2.dropped_keys == []  # (0,0) は残る


def test_resize_tolerance_is_1e_3_degrees():
    d = make_doc(cols=3, rows=1, yaw=90.0, mirror=False)
    put(d, 0, 2, {"A": 1.0})  # 90°
    s = af.resize_grid(d, 3, 1, yaw_range=90.0005)  # 0.0005° ずれ → 同じ点とみなす
    assert s.kept_keys == 1 and s.dropped_keys == []
    s = af.resize_grid(d, 3, 1, yaw_range=90.003)  # 0.0025° ずれ → 落とす
    assert s.kept_keys == 0 and len(s.dropped_keys) == 1


def test_resize_works_on_every_layer_and_validates_size():
    d = make_doc(mirror=False)
    d.layers.append(Layer(name="Happy"))
    put(d, 1, 2, {"A": 1.0}, layer=0)
    put(d, 1, 2, {"B": 1.0}, layer=1)
    af.resize_grid(d, 7, 3)
    assert d.layers[0].points[(1, 3)].pose.curves == {"A": 1.0}
    assert d.layers[1].points[(1, 3)].pose.curves == {"B": 1.0}
    with pytest.raises(ValueError):
        af.resize_grid(d, 0, 3)
    assert (d.grid.cols, d.grid.rows) == (7, 3)  # 失敗しても格子は変わらない


def test_resize_layer_without_keys_gets_no_points():
    d = make_doc()
    put(d, 0, 0, {"A": 1.0}, key=False)
    af.resize_grid(d, 3, 3)
    assert d.layers[0].points == {}


def test_resize_same_size_is_stable():
    d = _rich_doc()
    af.generate_from_keys(d, 0)
    keys_before = {k: p.pose for k, p in d.layers[0].points.items() if p.is_key}
    s = af.resize_grid(d, 5, 3)
    assert s.dropped_keys == [] and s.kept_keys == len(keys_before)
    assert {k: p.pose for k, p in d.layers[0].points.items() if p.is_key} == keys_before


# --- copy_from ---


def _src():
    s = make_doc(cols=5, rows=3, mirror=False)
    s.layers.append(Layer(name="Happy"))
    put(s, 1, 3, {"A": 1.0}, {"B1": bone(t=(1, 0, 0))}, layer=0)
    put(s, 1, 0, {"G": 0.5}, key=False, layer=0)
    put(s, 1, 2, {"H": 1.0}, layer=1)
    return s


def test_copy_parse_mode():
    assert af.parse_copy_mode("keys_only") == {"keys_only"}
    assert af.parse_copy_mode("all_layers+keys_only") == {"all_layers", "keys_only"}
    assert af.parse_copy_mode(["working_set_only"]) == {"working_set_only"}
    with pytest.raises(ValueError):
        af.parse_copy_mode("everything")


def test_copy_resamples_keys_onto_destination_grid_as_non_keys():
    s = _src()
    d = make_doc(cols=3, rows=3, yaw=60, pitch=30, mirror=False)
    put(d, 1, 1, {"Old": 1.0})  # 宛先にあったキーも上書きされ、キーでなくなる（UE 準拠）
    r = af.copy_from(s, d, "keys_only")
    assert r.copied_layers == 1 and r.copied_points == 9 and r.created_layers == []
    pts = d.layers[0].points
    assert len(pts) == 9 and not any(p.is_key for p in pts.values())
    assert set(pts[(1, 1)].pose.curves) == {"A"}  # 唯一のキー → 全点が A を持つ（IDW の alpha = 1）
    assert pts[(1, 1)].pose.curves["A"] == pytest.approx(1.0)
    assert "G" not in pts[(0, 0)].pose.curves  # keys_only: 非キーの G は使わない
    assert pts[(1, 1)].pose.bones["B1"].t == pytest.approx((1.0, 0.0, 0.0))
    assert s.layers[0].points[(1, 3)].is_key  # ソースは変えない


def test_copy_without_keys_only_uses_generated_points_too():
    s = _src()
    d = make_doc(cols=3, rows=1, mirror=False)
    af.copy_from(s, d, set())
    assert "G" in d.layers[0].points[(0, 0)].pose.curves  # G は (-90, 0)、宛先 (0,0) の角度 (-90, 0) と一致 → 完全一致


def test_copy_exact_hit_between_different_grids():
    s = make_doc(cols=5, rows=3, mirror=False)
    put(s, 1, 3, {"A": 1.0}, {"B": bone(s=(2, 2, 2), t=(1, 0, 0))})  # (45, 0)
    d = make_doc(cols=9, rows=5, mirror=False)  # col 6 = 45°, row 2 = 0°
    af.copy_from(s, d, "keys_only")
    p = d.layers[0].points[(2, 6)]
    assert p.pose.curves == {"A": 1.0} and p.pose.bones["B"].s == (2, 2, 2)


def test_copy_all_layers_matches_by_name_and_creates_missing():
    s = _src()
    d = make_doc(cols=3, rows=3, mirror=False)
    r = af.copy_from(s, d, "all_layers+keys_only")
    assert r.copied_layers == 2 and r.created_layers == ["Happy"]
    assert [l.name for l in d.layers] == ["Neutral", "Happy"]
    assert d.layers[1].points[(1, 1)].pose.curves["H"] == pytest.approx(1.0)
    # 既にある名前は同じレイヤーへ
    d2 = make_doc(cols=3, rows=3, mirror=False)
    d2.layers.append(Layer(name="Happy", emotion_curve="happy_curve"))
    r2 = af.copy_from(s, d2, "all_layers+keys_only")
    assert r2.created_layers == [] and len(d2.layers) == 2 and d2.layers[1].emotion_curve == "happy_curve"
    assert d2.layers[1].points


def test_copy_all_layers_respects_layer_limit():
    s = make_doc(mirror=False)
    for i in range(MAX_LAYERS + 2):
        s.layers.append(Layer(name=f"L{i}"))
        put(s, 1, 2, {"A": 1.0}, layer=len(s.layers) - 1)
    d = make_doc(mirror=False)
    r = af.copy_from(s, d, "all_layers+keys_only")
    assert len(d.layers) == MAX_LAYERS
    assert len(r.layer_limit_reached) == len(s.layers) - MAX_LAYERS
    assert r.skipped_layers == ["Neutral"]  # Neutral にはキーが無い


def test_copy_single_layer_by_index():
    s = _src()
    d = make_doc(cols=3, rows=3, mirror=False)
    d.layers.append(Layer(name="Sad"))
    r = af.copy_from(s, d, "keys_only", src_layer_index=1, dst_layer_index=1)
    assert r.copied_layers == 1 and d.layers[0].points == {}
    assert d.layers[1].points[(0, 0)].pose.curves["H"] == pytest.approx(1.0)
    with pytest.raises(IndexError):
        af.copy_from(s, d, "keys_only", src_layer_index=9)


def test_copy_working_set_only_filters_each_kind_when_target_set_is_not_empty():
    s = make_doc(mirror=False)
    put(s, 1, 2, {"A": 1.0, "Z": 1.0}, {"B1": bone(t=(1, 0, 0)), "B2": bone(t=(2, 0, 0))})
    d = make_doc(cols=3, rows=1, mirror=False)
    d.working_set.curves = ["A"]  # ボーンの作業セットは空 → ボーンは絞らない
    af.copy_from(s, d, "keys_only+working_set_only")
    pose = d.layers[0].points[(0, 0)].pose
    assert set(pose.curves) == {"A"} and set(pose.bones) == {"B1", "B2"}
    d2 = make_doc(cols=3, rows=1, mirror=False)
    d2.working_set.bones = ["B2"]
    af.copy_from(s, d2, "keys_only+working_set_only")
    assert set(d2.layers[0].points[(0, 0)].pose.curves) == {"A", "Z"}
    assert set(d2.layers[0].points[(0, 0)].pose.bones) == {"B2"}
    d3 = make_doc(cols=3, rows=1, mirror=False)
    d3.working_set.curves = ["A"]
    af.copy_from(s, d3, "keys_only")  # 旗が無ければ絞らない
    assert set(d3.layers[0].points[(0, 0)].pose.curves) == {"A", "Z"}


def test_copy_names_missing_in_destination_are_copied_fail_soft():
    s = make_doc(mirror=False)
    put(s, 1, 2, {"NoSuchShape": 1.0}, {"NoSuchBone": bone(t=(1, 0, 0))})
    d = make_doc(cols=3, rows=1, mirror=False)
    d.working_set.curves = ["Other"]
    af.copy_from(s, d, "keys_only")  # 作業セットで絞らないので、名前の存在確認なしでそのまま入る（検証は別）
    assert "NoSuchShape" in d.layers[0].points[(0, 0)].pose.curves


def test_copy_without_samples_and_with_self_is_noop():
    s = make_doc()
    d = make_doc()
    r = af.copy_from(s, d, "keys_only")
    assert r.copied_layers == 0 and r.skipped_layers == ["Neutral"] and d.layers[0].points == {}
    put(s, 1, 2, {"A": 1.0})
    before = snapshot(s)
    assert af.copy_from(s, s, "keys_only").copied_points == 0
    assert snapshot(s) == before


def test_copy_uses_destination_fill_settings():
    s = make_doc(cols=5, rows=1, mirror=False)
    put(s, 0, 0, {"A": 1.0})
    put(s, 0, 4, {"B": 1.0})
    d = make_doc(cols=3, rows=1, mirror=False, mode="NearestKey")
    af.copy_from(s, d, "keys_only")
    assert d.layers[0].points[(0, 1)].pose.curves == {"A": 1.0}  # 同距離 → 先のキー
    assert d.layers[0].points[(0, 2)].pose.curves == {"B": 1.0}


def test_copy_converts_bones_between_spaces():
    s = make_doc(cols=3, rows=1, mirror=False)
    s.meta = Meta(unit="cm", up_axis="Y", handedness="right")  # Maya
    put(s, 0, 1, bones={"B": bone(t=(1.0, 2.0, 3.0), r=qz(20))})
    d = make_doc(cols=3, rows=1, mirror=False)  # UE
    af.copy_from(s, d, "keys_only")
    got = d.layers[0].points[(0, 0)].pose.bones["B"]
    want = sp.convert_bone_offset(s.layers[0].points[(0, 1)].pose.bones["B"], sp.MAYA, sp.UE)
    assert got.t == pytest.approx(want.t) and got.r == pytest.approx(want.r)


# --- 共通のテストデータ（conformance/autofill.json）---


def _load_cases():
    data = json.loads((CONF / "autofill.json").read_text(encoding="utf-8"))
    assert data["kind"] == "autofill"
    return [pytest.param(c, id=c["name"]) for c in data["cases"]]


def _doc_from_case(c):
    g, m, a = c["grid"], c["mirror"], c["autogen"]
    d = Document()
    d.grid = Grid(yaw_range=g["yawRange"], pitch_range=g["pitchRange"], cols=g["cols"], rows=g["rows"])
    d.mirror = Mirror(m["enabled"], m["suffixL"], m["suffixR"], list(m["exclude"]), m["boneAxis"])
    d.autogen = Autogen(a["mode"], float(a["idwPower"]))
    pts = {}
    for k in c["keys"]:
        bones = {
            n: BoneOffset(tuple(b["t"]), tuple(b["r"]), tuple(b.get("s", [1.0, 1.0, 1.0])))
            for n, b in k["bones"].items()
        }
        pts[(k["row"], k["col"])] = GridPoint(k["row"], k["col"], True, SourcePose(dict(k["curves"]), bones))
    d.layers = [Layer(points=pts)]
    return d


def _expect_pose(e):
    return SourcePose(
        dict(e["curves"]),
        {n: BoneOffset(tuple(b["t"]), tuple(b["r"]), tuple(b["s"])) for n, b in e["bones"].items()},
    )


def test_conformance_file_is_well_formed():
    data = json.loads((CONF / "autofill.json").read_text(encoding="utf-8"))
    names = [c["name"] for c in data["cases"]]
    assert len(names) == len(set(names))
    assert all(c["source"] == "python-port" for c in data["cases"])
    assert {c["op"] for c in data["cases"]} == {"generate", "interpolate"}
    gen = [c for c in data["cases"] if c["op"] == "generate"]
    assert {c["autogen"]["mode"] for c in gen} == {"IDW", "NearestKey"}
    assert {c["autogen"]["idwPower"] for c in gen if c["autogen"]["mode"] == "IDW"} >= {1, 2, 4}
    assert {c["mirror"]["enabled"] for c in gen} == {True, False}
    assert {(c["grid"]["cols"], c["grid"]["rows"]) for c in gen} >= {(5, 3), (3, 3)}


@pytest.mark.parametrize("case", _load_cases())
def test_conformance_autofill(case):
    d = _doc_from_case(case)
    tol = case.get("tolerance", TOL)
    if case["op"] == "interpolate":
        got = af.interpolate_pose_at_angles(d, 0, case["yaw"], case["pitch"], mirror=case.get("useMirror", False))
        pose_close(got, _expect_pose(case["expect"]["pose"]), tol)
        return
    keys_before = {k: copy.deepcopy(p.pose) for k, p in d.layers[0].points.items()}
    summary = af.generate_from_keys(d, 0)
    exp = case["expect"]
    assert summary.keys == exp["summary"]["keys"]
    assert summary.mirror_keys == exp["summary"]["mirrorKeys"]
    assert summary.generated == exp["summary"]["generated"]
    got_pts = {k: p for k, p in d.layers[0].points.items() if not p.is_key}
    assert set(got_pts) == {(e["row"], e["col"]) for e in exp["points"]}  # 期待に無い点が出てはいけない
    for e in exp["points"]:
        pose_close(got_pts[(e["row"], e["col"])].pose, _expect_pose(e), tol)
    for k, pose in keys_before.items():
        assert d.layers[0].points[k].is_key and d.layers[0].points[k].pose == pose
