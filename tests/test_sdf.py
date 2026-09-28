import math

import pytest

np = pytest.importorskip("numpy")

from tdrive_toon import sdf  # noqa: E402


def test_distance_transform_matches_brute_force():
    rng = np.random.default_rng(0)
    feat = rng.random((24, 31)) < 0.05
    feat[0, 0] = True
    d = sdf.distance_to(feat)
    ys, xs = np.nonzero(feat)
    for y in range(0, 24, 5):
        for x in range(0, 31, 7):
            brute = min(math.hypot(y - fy, x - fx) for fy, fx in zip(ys, xs))
            assert math.isclose(d[y, x], brute, abs_tol=1e-9)


def _vertical_edge_masks(width=64, height=8, edges=(48, 32, 16)):
    """右から照らすほど明るい範囲（右側）が狭くなる: 0°=x<48、90°=x<32 … のようなマスク。"""
    xs = np.arange(width)[None, :].repeat(height, 0)
    return [xs < e for e in edges]


def test_combine_interpolates_boundary_between_angles():
    masks = _vertical_edge_masks()
    value = sdf.combine(masks, [0, 90, 180])
    row = value[4]
    # 0° の境界 x=48 と 90° の境界 x=32 の間は 0 → 0.5 へ単調に増える（x が小さいほど長く明るい）
    band = row[32:48]
    assert np.all(np.diff(band) <= 1e-9)  # x が増えるほど値（明るくいられる角度）は小さくなる
    assert 0.0 <= band.min() and band.max() <= 0.5 + 1e-9
    # 中間の x=40 はおよそ 45°（0.25）
    assert abs(row[40] - 0.25) < 0.05
    # 常に明るい所（x < 16）は最後の角度（180° = 1.0）
    assert row[5] == 1.0
    # 0° でも暗い所（x ≥ 48）は 0
    assert row[60] == 0.0


def test_shader_judgement_moves_shadow_with_light_angle():
    masks = _vertical_edge_masks()
    value = sdf.combine(masks, [0, 90, 180])[4]
    lit_at_45 = [sdf.expected_lit(v, 0.25) for v in value]
    lit_at_90 = [sdf.expected_lit(v, 0.5) for v in value]
    # ライトが後ろに回るほど明るい範囲は狭くなる
    assert sum(lit_at_90) < sum(lit_at_45)
    assert 30 <= sum(lit_at_90) <= 34  # 90° の境界 x=32 付近


def test_inclusion_is_enforced():
    a = np.zeros((4, 10), bool)
    a[:, :6] = True
    b = np.zeros((4, 10), bool)
    b[:, 3:9] = True  # 包含関係が崩れたマスク
    value = sdf.combine([a, b], [0, 180])
    assert value[0, 8] == 0.0  # 0° で暗い所は、後ろの角度で白くても明るくならない


def test_angle_from_filename_and_light_angle():
    assert sdf.angle_from_filename("face_shadow_030.png") == 30.0
    with pytest.raises(ValueError):
        sdf.angle_from_filename("face.png")
    assert math.isclose(sdf.light_angle01((0, 0, 1), (1, 0, 0)), 0.5)
    assert math.isclose(sdf.light_angle01((0, 0, 1), (0, 0, -1)), 1.0)
