"""SDF 顔影マップの合成（Maya 非依存、numpy のみ。docs/05 §3.3、T-21）。

角度ごとの白黒マスク（白 = その角度で明るい）から、各画素が「明るいままでいられる最大の角度（0〜1）」を求める。
隣り合う角度のマスクの符号付き距離を線形補間して、境界が角度に応じてなめらかに動くようにする。
"""

from __future__ import annotations

import math

import numpy as np

_INF = 1e20


def _edt_1d(f: np.ndarray) -> np.ndarray:
    """1 次元の二乗距離変換（Felzenszwalb & Huttenlocher）。f: 0 = 特徴点、_INF = それ以外。"""
    n = len(f)
    d = np.empty(n)
    v = np.zeros(n, dtype=np.int64)
    z = np.empty(n + 1)
    k = 0
    v[0] = 0
    z[0], z[1] = -_INF, _INF
    for q in range(1, n):
        while True:
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
            if s <= z[k]:
                k -= 1
                if k < 0:
                    k = 0
                    break
                continue
            break
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = _INF
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d


def distance_to(feature: np.ndarray) -> np.ndarray:
    """feature（True の画素）までのユークリッド距離（画素）。"""
    f = np.where(feature, 0.0, _INF)
    tmp = np.empty_like(f)
    for x in range(f.shape[1]):
        tmp[:, x] = _edt_1d(f[:, x])
    out = np.empty_like(f)
    for y in range(f.shape[0]):
        out[y, :] = _edt_1d(tmp[y, :])
    return np.sqrt(out)


def signed_distance(lit: np.ndarray) -> np.ndarray:
    """明るい領域の内側で正、外側で負の符号付き距離（境界は ±0.5 付近）。"""
    if lit.all():
        return np.full(lit.shape, float(max(lit.shape)))
    if not lit.any():
        return np.full(lit.shape, -float(max(lit.shape)))
    return distance_to(~lit) - distance_to(lit)


def combine(masks: list[np.ndarray], angles_deg: list[float]) -> np.ndarray:
    """角度順のマスク（bool、True = 明るい）から顔影マップ（0〜1 = 角度 / 180°）を作る。

    角度が大きいほど明るい範囲が狭い（包含関係）ことが前提。崩れている画素は単調になるよう補正する。
    """
    if len(masks) != len(angles_deg) or len(masks) < 2:
        raise ValueError("マスクと角度は 2 枚以上・同じ数が必要")
    order = sorted(range(len(masks)), key=lambda i: angles_deg[i])
    masks = [masks[i] for i in order]
    angles = [angles_deg[i] / 180.0 for i in order]
    # 包含関係を強制（後ろのマスクは前のマスクの内側）
    fixed = [masks[0]]
    for m in masks[1:]:
        fixed.append(m & fixed[-1])
    sd = [signed_distance(m) for m in fixed]
    value = np.zeros(masks[0].shape)
    value[fixed[-1]] = 1.0 if angles[-1] >= 1.0 else angles[-1]
    for k in range(len(fixed) - 1):
        a0, a1 = angles[k], angles[k + 1]
        band = fixed[k] & ~fixed[k + 1]  # 角度 k では明るく、k+1 では暗い画素
        d0, d1 = sd[k][band], sd[k + 1][band]
        t = np.clip(d0 / np.maximum(d0 - d1, 1e-6), 0.0, 1.0)
        value[band] = a0 + (a1 - a0) * t
    return value


def angle_from_filename(name: str) -> float:
    """face_shadow_030.png → 30.0（数字の最後のまとまりを角度とみなす）。"""
    import re

    nums = re.findall(r"(\d+(?:\.\d+)?)", name)
    if not nums:
        raise ValueError(f"ファイル名に角度（度）がありません: {name}")
    a = float(nums[-1])
    if not 0 <= a <= 180:
        raise ValueError(f"角度は 0〜180: {name}")
    return a


def expected_lit(value: float, angle01: float, feather: float = 0.0) -> float:
    """シェーダーの判定（smoothstep）と同じ式。テスト用。"""
    if feather <= 0:
        return 1.0 if value >= angle01 else 0.0
    t = min(max((value - (angle01 - feather)) / (2 * feather), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def light_angle01(face_forward: tuple[float, float, float], to_light: tuple[float, float, float]) -> float:
    """水平面での正面とライト方向の角度（0 = 正面、1 = 真後ろ）。シェーダーと同じ。"""
    fx, fz, lx, lz = face_forward[0], face_forward[2], to_light[0], to_light[2]
    lf, ll = math.hypot(fx, fz), math.hypot(lx, lz)
    if lf < 1e-9 or ll < 1e-9:
        return 0.0
    c = max(-1.0, min(1.0, (fx * lx + fz * lz) / (lf * ll)))
    return math.acos(c) / math.pi
