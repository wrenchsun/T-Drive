import math

import pytest

from tdrive_toon import envmath


def approx(v, expected):
    return all(math.isclose(a, b, abs_tol=1e-4) for a, b in zip(v, expected))


@pytest.mark.parametrize(
    "euler,to_light_maya",
    [
        ((90, 0, 0), (0, 1, 0)),      # 真下へ照らすライト → 光源は真上
        ((0, 0, 0), (0, 0, -1)),      # +Z へ照らす → 光源はキャラクターの背後（-Z）
        ((0, 180, 0), (0, 0, 1)),     # -Z へ照らす → 光源はキャラクターの正面（+Z）
        ((0, 90, 0), (1, 0, 0)),      # Unity +X へ照らす → 光源は Unity -X = Maya +X
        ((0, 0, 45), (0, 0, -1)),     # Z 回転は方向に影響しない
    ],
)
def test_light_direction(euler, to_light_maya):
    assert approx(envmath.light_dir_to_light_maya(euler), to_light_maya)


def test_light_direction_ms2026_ingame_example():
    # InGame のライト (50, -30, 0): 上から、キャラクターの背後・Unity の -X 側から照らす
    d = envmath.light_dir_to_light_maya((50, -30, 0))
    assert approx(d, (-math.cos(math.radians(50)) * math.sin(math.radians(30)), math.sin(math.radians(50)),
                      -math.cos(math.radians(50)) * math.cos(math.radians(30))))


def test_focal_length_matches_vertical_fov():
    vfa = 0.945  # Maya 既定（35mm フル）
    fl = envmath.focal_length_for_vertical_fov(40.0, vfa)
    fov_back = math.degrees(2 * math.atan(vfa * 25.4 / 2 / fl))
    assert math.isclose(fov_back, 40.0, abs_tol=1e-6)


def test_orbit_camera_looks_at_target():
    pos, rot = envmath.orbit_camera((0, 150, 0), 100, yaw_deg=90)
    assert approx(pos, (100, 150, 0))
    assert approx(rot, (0, 90, 0))  # Maya カメラは -Z を見るので Y+90 で -X（= target 方向）を向く


def test_srgb_to_linear_matches_unity_curve():
    assert envmath.srgb_to_linear(0.0) == 0.0
    assert math.isclose(envmath.srgb_to_linear(1.0), 1.0)
    assert math.isclose(envmath.srgb_to_linear(0.5), 0.21404, abs_tol=1e-4)
    assert envmath.srgb_color_to_linear([0.5, 0.5, 0.5, 0.5])[3] == 0.5  # α は変換しない


def test_units_per_meter():
    assert envmath.units_per_meter("cm") == 100.0
    assert envmath.units_per_meter("m") == 1.0
    assert math.isclose(envmath.units_per_meter("in"), 39.3700787, rel_tol=1e-6)
    with pytest.raises(ValueError):
        envmath.units_per_meter("furlong")


@pytest.mark.parametrize("angle,front,tq,side", [(0, 1, 0, 0), (45, 0, 1, 0), (90, 0, 0, 1), (135, 0, 0, 1), (-45, 0, 1, 0)])
def test_view_correction_weights_key_angles(angle, front, tq, side):
    w = envmath.view_correction_weights(angle)
    assert approx((w["front"], w["threeQuarter"], w["side"]), (front, tq, side))


def test_view_correction_weights_sum_to_one():
    for a in range(0, 91, 5):
        w = envmath.view_correction_weights(a)
        assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-9)


def test_horizontal_angle():
    assert math.isclose(envmath.horizontal_angle_deg((0, 0, 1), (1, 5, 0)), 90.0)  # 高さは無視
    assert math.isclose(envmath.horizontal_angle_deg((0, 0, 1), (0, 0, -3)), 180.0)


def test_light_stabilizer_hysteresis_ignores_small_jitter():
    st = envmath.LightStabilizer(smoothing=0.0, hysteresis_deg=3.0)
    base = (0.0, 0.0, 1.0)
    st.update(base, 0.033)
    tilt = lambda deg: (math.sin(math.radians(deg)), 0.0, math.cos(math.radians(deg)))
    for deg in (1.0, -2.0, 2.9, -1.5):
        assert envmath.angle_deg(st.update(tilt(deg), 0.033), base) < 1e-6  # 揺れは無視
    assert envmath.angle_deg(st.update(tilt(5.0), 0.033), tilt(5.0)) < 1e-6  # 超えたら追従（平滑化 0 = 即時）


def test_light_stabilizer_smoothing_time_constant():
    st = envmath.LightStabilizer(smoothing=0.5, hysteresis_deg=0.0)
    st.update((0.0, 0.0, 1.0), 0.0)
    d = st.update((1.0, 0.0, 0.0), 0.5)  # 時定数 1 回分 → 90° の 1 - e^-1 ≒ 63.2% 進む
    assert envmath.angle_deg(d, (0.0, 0.0, 1.0)) == pytest.approx(90.0 * (1 - math.exp(-1)), abs=1e-6)
    for _ in range(200):
        d = st.update((1.0, 0.0, 0.0), 0.033)
    assert st.settled()


def test_light_stabilizer_frame_rate_independent():
    a = envmath.LightStabilizer(0.3, 0.0)
    b = envmath.LightStabilizer(0.3, 0.0)
    for s in (a, b):
        s.update((0.0, 0.0, 1.0), 0.0)
    da = a.update((1.0, 0.0, 0.0), 0.2)
    for _ in range(4):
        db = b.update((1.0, 0.0, 0.0), 0.05)
    assert envmath.angle_deg(da, db) < 1e-6  # 同じ時間なら刻み方によらない（同一平面の slerp）
