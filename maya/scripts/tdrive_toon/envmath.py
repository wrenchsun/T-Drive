"""Unity ⇔ Maya の座標・カメラ換算（Maya 非依存。tests/ で単体テスト）。

- Unity: 左手系 Y-up、1 unit = 1 m。Maya: 右手系 Y-up、cm
- FBX で Maya → Unity に渡すと X が反転する（キャラクターはどちらでも +Z を向く）
  → 方向ベクトルは Unity (x, y, z) = Maya (-x, y, z)
- Unity のオイラー角は Z → X → Y の順に適用（R = Ry · Rx · Rz）
"""

from __future__ import annotations

import math

UNITY_TO_MAYA_LENGTH = 100.0  # m → cm


def unity_euler_forward(euler_deg: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unity の回転 (x, y, z 度) を掛けた forward (0,0,1)。Unity 座標系で返す。"""
    x, y, _z = (math.radians(a) for a in euler_deg)  # Z 回転は forward に影響しない
    # Rx: (0,0,1) → (0, -sin x, cos x)。Ry: (a,b,c) → (a cos y + c sin y, b, -a sin y + c cos y)
    fy = -math.sin(x)
    fz = math.cos(x)
    return (fz * math.sin(y), fy, fz * math.cos(y))


def unity_to_maya_dir(v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (-v[0], v[1], v[2])


def maya_to_unity_dir(v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (-v[0], v[1], v[2])


def light_dir_to_light_maya(unity_euler_deg: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unity の Directional Light の回転から「表面 → 光源」方向（Maya 座標）を求める。

    Directional Light は forward 方向へ光が進むので、光源へ向かう方向は -forward。
    """
    f = unity_euler_forward(unity_euler_deg)
    return unity_to_maya_dir((-f[0], -f[1], -f[2]))


def focal_length_for_vertical_fov(vertical_fov_deg: float, vertical_aperture_inch: float) -> float:
    """Unity の縦 FOV（Gate Fit = Vertical 相当）になる Maya の焦点距離 (mm)。"""
    half = math.radians(vertical_fov_deg) / 2.0
    return (vertical_aperture_inch * 25.4 / 2.0) / math.tan(half)


def orbit_camera(
    target: tuple[float, float, float], distance: float, yaw_deg: float, pitch_deg: float = 0.0
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """target を見るカメラの (位置, Maya 回転 XYZ 度)。yaw 0 = キャラクター正面（+Z 側）から見る。"""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    pos = (
        target[0] + math.sin(yaw) * math.cos(pitch) * distance,
        target[1] + math.sin(pitch) * distance,
        target[2] + math.cos(yaw) * math.cos(pitch) * distance,
    )
    return pos, (-pitch_deg, yaw_deg, 0.0)
