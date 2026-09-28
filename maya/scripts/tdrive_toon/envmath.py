"""Unity ⇔ Maya の座標・カメラ換算（Maya 非依存。tests/ で単体テスト）。

- Unity: 左手系 Y-up、1 unit = 1 m。Maya: 右手系 Y-up、cm
- FBX で Maya → Unity に渡すと X が反転する（キャラクターはどちらでも +Z を向く）
  → 方向ベクトルは Unity (x, y, z) = Maya (-x, y, z)
- Unity のオイラー角は Z → X → Y の順に適用（R = Ry · Rx · Rz）
"""

from __future__ import annotations

import math

# Maya のシーン単位（cmds.currentUnit(linear=True) の値）1 m あたりの数。Unity は 1 unit = 1 m
_UNITS_PER_METER = {
    "mm": 1000.0, "millimeter": 1000.0,
    "cm": 100.0, "centimeter": 100.0,
    "m": 1.0, "meter": 1.0,
    "km": 0.001, "kilometer": 0.001,
    "in": 1 / 0.0254, "inch": 1 / 0.0254,
    "ft": 1 / 0.3048, "foot": 1 / 0.3048,
    "yd": 1 / 0.9144, "yard": 1 / 0.9144,
}


def units_per_meter(maya_linear_unit: str) -> float:
    """Unity の 1 m が Maya のシーン単位でいくつか。cm 固定を前提にしない（m 単位のシーンでも一致させる）。"""
    try:
        return _UNITS_PER_METER[maya_linear_unit]
    except KeyError:
        raise ValueError(f"未対応の Maya 長さ単位: {maya_linear_unit}") from None


def srgb_to_linear(c: float) -> float:
    """Unity (Linear 色空間) が Color プロパティ・ライト色をシェーダーへ渡す前に行う変換と同じ。"""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def srgb_color_to_linear(rgba):
    """RGB だけ変換し、アルファはそのまま（Unity と同じ）。"""
    v = [float(x) for x in rgba]
    return [srgb_to_linear(x) for x in v[:3]] + v[3:]


def _smooth(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def view_correction_weights(angle_deg: float) -> dict[str, float]:
    """カメラ角度（キャラクター正面との水平角、度）→ 補正 BlendShape のウェイト（docs/05 §3.2）。

    Unity の ToonCharacter も同じ式を使う。0°〜90° では和が 1。
    """
    a = min(abs(angle_deg), 180.0)
    front = 1.0 - _smooth(a / 45.0)
    if a <= 45.0:
        three_quarter = _smooth(a / 45.0)
        side = 0.0
    else:
        three_quarter = 1.0 - _smooth((a - 45.0) / 45.0)
        side = _smooth(min(a - 45.0, 45.0) / 45.0)
    return {"front": front, "threeQuarter": three_quarter, "side": side}


def horizontal_angle_deg(forward: tuple[float, float, float], to_camera: tuple[float, float, float]) -> float:
    """水平面（XZ）上での forward と to_camera の角度（0〜180 度）。"""
    fx, fz = forward[0], forward[2]
    cx, cz = to_camera[0], to_camera[2]
    lf, lc = math.hypot(fx, fz), math.hypot(cx, cz)
    if lf < 1e-9 or lc < 1e-9:
        return 0.0
    c = max(-1.0, min(1.0, (fx * cx + fz * cz) / (lf * lc)))
    return math.degrees(math.acos(c))


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


# ---------------------------------------------------------------- 影の安定化（T-17、docs/08 §3.3）
Vec3 = tuple[float, float, float]


def _normalize(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 0 else (0.0, 0.0, 1.0)


def angle_deg(a: Vec3, b: Vec3) -> float:
    a, b = _normalize(a), _normalize(b)
    d = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
    return math.degrees(math.acos(d))


def slerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    a, b = _normalize(a), _normalize(b)
    omega = math.radians(angle_deg(a, b))
    if omega < 1e-6:
        return b
    if math.pi - omega < 1e-6:  # 正反対は補間の向きが決まらないので線形（正規化）で代用
        return _normalize(tuple(x + (y - x) * t for x, y in zip(a, b)))
    s = math.sin(omega)
    wa, wb = math.sin((1 - t) * omega) / s, math.sin(t * omega) / s
    return _normalize(tuple(wa * x + wb * y for x, y in zip(a, b)))


class LightStabilizer:
    """キャラクターライトの平滑化・ヒステリシス。Unity の ToonLightRig と同じ式（C# はこれを移植する）。"""

    def __init__(self, smoothing: float, hysteresis_deg: float) -> None:
        self.smoothing = float(smoothing)
        self.hysteresis_deg = float(hysteresis_deg)
        self.anchor: Vec3 | None = None
        self.current: Vec3 | None = None

    def reset(self, direction: Vec3) -> None:
        self.anchor = self.current = _normalize(direction)

    def update(self, target: Vec3, dt: float) -> Vec3:
        t = _normalize(target)
        if self.current is None or self.anchor is None:
            self.reset(t)
            return t
        if angle_deg(t, self.anchor) > self.hysteresis_deg:
            self.anchor = t
        k = 1.0 if self.smoothing <= 0 else 1.0 - math.exp(-max(dt, 0.0) / self.smoothing)
        self.current = slerp(self.current, self.anchor, k)
        return self.current

    def settled(self, eps_deg: float = 0.01) -> bool:
        """出力が基準方向に追いついた（これ以上動かない）。"""
        return self.current is not None and angle_deg(self.current, self.anchor) <= eps_deg
