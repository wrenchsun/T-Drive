"""座標系の変換（Maya / Unity / UE ⇔ 正準空間）。Maya 非依存。**ここだけが座標系を知っている。**

正準空間 = UE 準拠（cm / Z-up / 左手 / キャラクターの前 +X、右 +Y、上 +Z）。計算（evaluate.py）はここで定義する。

## 系の記述
`.fcpose.json` の `meta` と同じ {unit, upAxis, handedness}。

| 系 | unit | upAxis | handedness | キャラクターの前 / 左 / 上（慣例） |
|---|---|---|---|---|
| Maya | cm | Y | right | +Z / +X / +Y |
| Unity | m | Y | left | +Z / -X / +Y |
| UE（正準） | cm | Z | left | +X / -Y / +Z |
| Blender 型（Z-up 右手） | m | Z | right | +Y / -X / +Z |

## 軸の対応（系 → 正準。署名付きの軸の入れ替え 3x3 行列 M。canonical = M · v）
- upAxis = Y, 左手（Unity）  : (x, y, z) → (z,  x, y)
- upAxis = Y, 右手（Maya）   : (x, y, z) → (z, -x, y)
- upAxis = Z, 左手（UE）     : (x, y, z) → (x,  y, z)  （恒等）
- upAxis = Z, 右手（Blender）: (x, y, z) → (y,  x, z)
上は必ず上へ、慣例の「前」は +X へ行く。右手 → 左手（またはその逆）の変換は det(M) = -1 になる（鏡映）。
単位は cm へ換算（スケール = UNIT_TO_CM[unit]）。系から系への変換は「src → 正準 → dst」の合成
（M = M_dst^T · M_src。M は直交行列なので逆は転置）。

## 量ごとの変換（M, det = det(M), k = 長さの倍率）
- 位置        : p' = k · M p
- 方向        : d' = M d
- クォータニオン [x, y, z, w]: 回転 R を M R M^T へ。q' = (det · M v, w)（v は q の xyz）
  → 鏡映（det = -1）では回転の向きが逆になる。右手系の上軸まわり +θ は左手系では -θ
  （標準の四元数の回転式 v' = q v q* を両方の系で使うとき、位置の変換と回転が整合する）
- スケール s : s' = |M| s（成分の入れ替えのみ。符号は付けない）
- BoneOffset（親ボーン空間の加算。S → R → T）: t は位置と同じ k 倍、r、s は上のとおり。
  **ボーンのローカル軸はワールドと同じ軸変換に従う前提**（FBX 経由でそろった骨格の想定。
  `docs/14 §4.2` の「読む側が自分の系と違えばボーンのずらしを変換する」）
- centerOffset: 位置と同じ k 倍の変換（基準ボーンのローカルの値。方向と同様に軸を入れ替える）
- forwardAxis（`+X` … `-Z`）: 軸ベクトルを M で変換した軸名。上軸は前方向にできない（ValueError）
- mirror.boneAxis（`X` / `Y` / `Z`）: |M| で入れ替えた軸名（符号なし）。Maya の "X" ⇔ UE の "Y"
- シェイプの重み（curves）は変換不要
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from typing import Optional, Sequence, Union

from . import evaluate
from .model import BoneOffset, Document, Meta, PoseDocument, Quat, Vec3

# 長さの単位 → cm
UNIT_TO_CM = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "km": 100000.0,
    "in": 2.54,
    "ft": 30.48,
}


@dataclass(frozen=True)
class SpaceSpec:
    unit: str
    up_axis: str  # "Y" | "Z"
    handedness: str  # "left" | "right"


MAYA = SpaceSpec("cm", "Y", "right")
UNITY = SpaceSpec("m", "Y", "left")
UE = SpaceSpec("cm", "Z", "left")
CANONICAL = UE

NAMED = {"maya": MAYA, "unity": UNITY, "ue": UE, "canonical": CANONICAL}

SpaceLike = Union[SpaceSpec, Meta, str]

Matrix = tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]


def space_of(space: SpaceLike) -> SpaceSpec:
    """SpaceSpec / Meta / 名前（"maya" "unity" "ue" "canonical" または dict 風の meta）→ SpaceSpec。"""
    if isinstance(space, SpaceSpec):
        spec = space
    elif isinstance(space, Meta):
        spec = SpaceSpec(space.unit, space.up_axis, space.handedness)
    elif isinstance(space, str):
        try:
            spec = NAMED[space.lower()]
        except KeyError:
            raise ValueError(f"未知の座標系名: {space!r}") from None
    elif isinstance(space, dict):
        spec = SpaceSpec(
            space.get("unit", "cm"), space.get("upAxis", "Z"), space.get("handedness", "left")
        )
    else:
        raise TypeError(f"座標系を表せない値: {space!r}")
    if spec.unit not in UNIT_TO_CM:
        raise ValueError(f"未対応の単位: {spec.unit!r}")
    if spec.up_axis not in ("Y", "Z"):
        raise ValueError(f"未対応の upAxis: {spec.up_axis!r}")
    if spec.handedness not in ("left", "right"):
        raise ValueError(f"未対応の handedness: {spec.handedness!r}")
    return spec


def to_canonical_matrix(space: SpaceLike) -> Matrix:
    """系 → 正準の軸の入れ替え行列 M（canonical = M · v。長さの換算は含まない）。"""
    s = space_of(space)
    if s.up_axis == "Y":
        h = 1 if s.handedness == "left" else -1
        return ((0, 0, 1), (h, 0, 0), (0, 1, 0))
    if s.handedness == "left":
        return ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    return ((0, 1, 0), (1, 0, 0), (0, 0, 1))


def _det(m: Matrix) -> int:
    return round(
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _matmul(a: Matrix, b: Matrix) -> Matrix:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _transpose(m: Matrix) -> Matrix:
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def _apply(m: Matrix, v: Sequence[float]) -> tuple[float, float, float]:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


@dataclass(frozen=True)
class Converter:
    """src → dst の変換（軸の入れ替え行列 matrix・長さの倍率 scale・向きの反転 det）。"""

    matrix: Matrix
    scale: float
    det: int
    src_up_axis: str = "Z"  # 元の系の上軸（forwardAxis に使えない軸の判定用）

    def position(self, p: Sequence[float]) -> Vec3:
        x, y, z = _apply(self.matrix, p)
        return (x * self.scale, y * self.scale, z * self.scale)

    def direction(self, d: Sequence[float]) -> Vec3:
        return _apply(self.matrix, d)

    def quaternion(self, q: Sequence[float]) -> Quat:
        x, y, z = _apply(self.matrix, q[:3])
        return (self.det * x, self.det * y, self.det * z, float(q[3]))

    def scale_vector(self, s: Sequence[float]) -> Vec3:
        a = tuple(tuple(abs(c) for c in row) for row in self.matrix)
        return _apply(a, s)  # type: ignore[arg-type]

    def bone_offset(self, b: BoneOffset) -> BoneOffset:
        return BoneOffset(
            t=self.position(b.t),
            r=self.quaternion(b.r),
            s=self.scale_vector(b.s),
            extra=copy.deepcopy(b.extra),
        )

    def forward_axis(self, axis: str) -> str:
        vec = _AXIS_VECTORS.get(axis)
        if vec is None:
            raise ValueError(f"未知の forwardAxis: {axis!r}")
        if axis[1] == self.src_up_axis:
            raise ValueError(f"上軸 {axis!r} は forwardAxis にできません（上軸 = {self.src_up_axis}）")
        out = self.direction(vec)
        for name, v in _AXIS_VECTORS.items():
            if all(abs(a - b) < 1e-9 for a, b in zip(out, v)):
                return name
        raise AssertionError("signed permutation の結果が軸にならない")  # pragma: no cover

    def mirror_axis(self, axis: str) -> str:
        idx = "XYZ".find(axis)
        if idx < 0:
            raise ValueError(f"未知の mirror.boneAxis: {axis!r}")
        vec = [0.0, 0.0, 0.0]
        vec[idx] = 1.0
        out = _apply(tuple(tuple(abs(c) for c in row) for row in self.matrix), vec)  # type: ignore[arg-type]
        return "XYZ"[max(range(3), key=lambda i: out[i])]


_AXIS_VECTORS = {
    "+X": (1, 0, 0),
    "-X": (-1, 0, 0),
    "+Y": (0, 1, 0),
    "-Y": (0, -1, 0),
    "+Z": (0, 0, 1),
    "-Z": (0, 0, -1),
}


def converter(src: SpaceLike, dst: SpaceLike) -> Converter:
    s, d = space_of(src), space_of(dst)
    m = _matmul(_transpose(to_canonical_matrix(d)), to_canonical_matrix(s))
    return Converter(m, UNIT_TO_CM[s.unit] / UNIT_TO_CM[d.unit], _det(m), s.up_axis)


# --- 関数形（1 回だけ変換したいとき）---


def convert_position(p: Sequence[float], src: SpaceLike, dst: SpaceLike) -> Vec3:
    return converter(src, dst).position(p)


def convert_direction(d: Sequence[float], src: SpaceLike, dst: SpaceLike) -> Vec3:
    return converter(src, dst).direction(d)


def convert_quaternion(q: Sequence[float], src: SpaceLike, dst: SpaceLike) -> Quat:
    return converter(src, dst).quaternion(q)


def convert_bone_offset(b: BoneOffset, src: SpaceLike, dst: SpaceLike) -> BoneOffset:
    return converter(src, dst).bone_offset(b)


def convert_forward_axis(axis: str, src: SpaceLike, dst: SpaceLike) -> str:
    return converter(src, dst).forward_axis(axis)


def convert_mirror_axis(axis: str, src: SpaceLike, dst: SpaceLike) -> str:
    return converter(src, dst).mirror_axis(axis)


def forward_axis_to_canonical(axis: str, src: SpaceLike) -> str:
    """forwardAxis を正準空間（±X / ±Y）の軸名へ。"""
    return converter(src, CANONICAL).forward_axis(axis)


def convert_document(doc: Document, dst: SpaceLike) -> Document:
    """Document の座標系を dst へ変換した複製を返す（meta も dst に書き換える）。
    変換するのは grid.centerOffset / grid.forwardAxis / mirror.boneAxis / 全レイヤーのボーンのずらし。
    シェイプの重みと、それ以外のキーは変えない。meta と同じ系なら中身は同じ複製。"""
    out = copy.deepcopy(doc)
    cv = converter(doc.meta, dst)
    d = space_of(dst)
    out.meta = replace(out.meta, unit=d.unit, up_axis=d.up_axis, handedness=d.handedness)
    out.grid.center_offset = cv.position(doc.grid.center_offset)
    out.grid.forward_axis = cv.forward_axis(doc.grid.forward_axis)
    out.mirror.bone_axis = cv.mirror_axis(doc.mirror.bone_axis)
    for layer in out.layers:
        for point in layer.points.values():
            point.pose.bones = {name: cv.bone_offset(b) for name, b in point.pose.bones.items()}
    return out


def convert_pose_document(doc: PoseDocument, dst: SpaceLike) -> PoseDocument:
    """FacialPose の座標系を dst へ変換した複製を返す。"""
    out = copy.deepcopy(doc)
    cv = converter(doc.meta, dst)
    d = space_of(dst)
    out.meta = replace(out.meta, unit=d.unit, up_axis=d.up_axis, handedness=d.handedness)
    out.pose.bones = {name: cv.bone_offset(b) for name, b in doc.pose.bones.items()}
    return out


# --- クォータニオン・視点の補助 ---


def quat_normalize(q: Sequence[float]) -> Quat:
    n = math.sqrt(sum(c * c for c in q))
    if n == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def quat_from_axis_angle(axis: Sequence[float], angle_deg: float) -> Quat:
    n = math.sqrt(sum(c * c for c in axis))
    h = math.radians(angle_deg) * 0.5
    s = math.sin(h) / n
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def quat_multiply(a: Sequence[float], b: Sequence[float]) -> Quat:
    """a × b（b を先に適用してから a）。[x, y, z, w]。"""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def rotate_vector(q: Sequence[float], v: Sequence[float]) -> Vec3:
    """v' = q v q*（正規化済みの q。標準の式。右手系・左手系のどちらでも同じ式）。"""
    qx, qy, qz, qw = q
    # t = 2 (q.xyz × v);  v' = v + w t + q.xyz × t
    tx = 2.0 * (qy * v[2] - qz * v[1])
    ty = 2.0 * (qz * v[0] - qx * v[2])
    tz = 2.0 * (qx * v[1] - qy * v[0])
    return (
        v[0] + qw * tx + (qy * tz - qz * ty),
        v[1] + qw * ty + (qz * tx - qx * tz),
        v[2] + qw * tz + (qx * ty - qy * tx),
    )


def compute_view_angles_in_space(
    space: SpaceLike,
    head_pos: Sequence[float],
    head_rotation: Optional[Sequence[float]],
    forward_axis: str,
    viewer_pos: Sequence[float],
    center_offset: Sequence[float] = (0.0, 0.0, 0.0),
) -> tuple[float, float]:
    """各環境の座標のまま (Yaw, Pitch) を求める入口（共通テストデータの view_angles ケースが使う）。

    - 格子の中心 = head_pos + head_rotation で回した center_offset（UE 版 ComputeGridBasis と同じ。スケールは無視）
    - キャラクターの前方 = head_rotation で回した forward_axis の軸（head_rotation=None は無回転）
    - それらを正準空間へ変換 → 前方の水平成分から Yaw を取り evaluate.compute_view_angles へ
    forward_axis は space の軸名（Maya は ±Z / ±X など）。head_pos / viewer_pos は space の単位。
    """
    cv = converter(space, CANONICAL)
    q = tuple(head_rotation) if head_rotation is not None else (0.0, 0.0, 0.0, 1.0)
    center = tuple(
        h + o for h, o in zip(head_pos, rotate_vector(q, center_offset))
    )
    fwd_vec = rotate_vector(q, _AXIS_VECTORS[forward_axis])
    fwd = cv.direction(fwd_vec)
    forward_yaw = math.degrees(math.atan2(fwd[1], fwd[0]))
    return evaluate.compute_view_angles(cv.position(center), forward_yaw, cv.position(viewer_pos))


def mirror_axis_index(axis: str, default: int = 0) -> int:
    """鏡映の軸名（`doc.mirror.bone_axis`。"X" / "Y" / "Z"、大文字小文字は問わない）→ 軸の番号（0 / 1 / 2）。空・不正なら default（X）。

    左右に分ける・ミラーのシェイプ道具が、顔の左右の軸として使う（Maya の系の文書では、メッシュのオブジェクト空間の同じ軸。+軸の側が L）。"""
    return {"X": 0, "Y": 1, "Z": 2}.get(str(axis or "").strip().upper(), default)


def mirror_axis_text(axis: str) -> str:
    """表示用の軸名（"X" / "Y" / "Z"）。`mirror_axis_index` と同じ規則（不正なら X）。"""
    return "XYZ"[mirror_axis_index(axis)]

