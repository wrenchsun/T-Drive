"""スムーズ法線の計算とエンコード（Maya 非依存。docs/03 §5、T-06）。

復元側は shaders/ToonCore.hlsl の Toon_OctahedralDecode / Toon_SmoothNormalWS。式を変えるときは両方を同時に変える。
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Vec3 = tuple[float, float, float]


def normalize(v: Sequence[float]) -> Vec3:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def octahedral_encode(n: Sequence[float]) -> tuple[float, float]:
    """単位ベクトル → [-1, 1]² 。ToonCore の Toon_OctahedralDecode の逆。"""
    s = abs(n[0]) + abs(n[1]) + abs(n[2])
    x, y, z = n[0] / s, n[1] / s, n[2] / s
    if z < 0.0:
        x, y = (1.0 - abs(y)) * (1.0 if x >= 0.0 else -1.0), (1.0 - abs(x)) * (1.0 if y >= 0.0 else -1.0)
    return (x, y)


def octahedral_decode(e: Sequence[float]) -> Vec3:
    """ToonCore の Toon_OctahedralDecode と同じ式（テスト・検証用）。"""
    x, y = e[0], e[1]
    z = 1.0 - abs(x) - abs(y)
    t = max(0.0, min(1.0, -z))
    x += -t if x >= 0.0 else t
    y += -t if y >= 0.0 else t
    return normalize((x, y, z))


def tangent_frame(normal: Sequence[float], tangent: Sequence[float], binormal: Sequence[float]) -> tuple[Vec3, Vec3, float]:
    """(正規直交化した接線, 従法線, 符号)。符号は Unity の tangent.w と同じ意味（UV の裏返り）。

    シェーダー側は bitangent = cross(N, T) * sign で復元するので、それと一致する従法線を返す。
    """
    n = normalize(normal)
    t = normalize([tangent[i] - n[i] * dot(n, tangent) for i in range(3)])
    sign = 1.0 if dot(cross(n, t), binormal) >= 0.0 else -1.0
    b = tuple(c * sign for c in cross(n, t))
    return t, b, sign  # type: ignore[return-value]


def to_tangent_space(v: Sequence[float], normal: Sequence[float], tangent: Sequence[float], binormal: Sequence[float]) -> Vec3:
    t, b, _ = tangent_frame(normal, tangent, binormal)
    n = normalize(normal)
    return normalize((dot(v, t), dot(v, b), dot(v, n)))


def from_tangent_space(v_ts: Sequence[float], normal: Sequence[float], tangent: Sequence[float], binormal: Sequence[float]) -> Vec3:
    """ToonCore の Toon_SmoothNormalWS と同じ再構成（テスト・検証用）。"""
    t, b, _ = tangent_frame(normal, tangent, binormal)
    n = normalize(normal)
    return normalize([t[i] * v_ts[0] + b[i] * v_ts[1] + n[i] * v_ts[2] for i in range(3)])


def smooth_by_position(
    positions: Sequence[Sequence[float]], normals: Iterable[tuple[int, Sequence[float]]], tolerance: float = 1e-4
) -> dict[int, Vec3]:
    """同じ位置（許容差内）にある頂点の法線を平均する。

    normals は (頂点番号, 法線) の列（フェース頂点ごとの法線を全部渡す）。戻り値は 頂点番号 → スムーズ法線。
    位置で束ねるので、UV やハードエッジで分割された頂点も同じ法線になる（アウトラインが割れない）。
    """
    inv = 1.0 / tolerance
    key_of = [(round(p[0] * inv), round(p[1] * inv), round(p[2] * inv)) for p in positions]
    acc: dict[tuple[int, int, int], list[float]] = {}
    for vid, n in normals:
        a = acc.setdefault(key_of[vid], [0.0, 0.0, 0.0])
        a[0] += n[0]
        a[1] += n[1]
        a[2] += n[2]
    return {vid: normalize(acc[key_of[vid]]) for vid in range(len(positions)) if key_of[vid] in acc}
