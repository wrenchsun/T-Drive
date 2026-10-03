"""`.fcpose.json` の読み書き（Maya 非依存）。形式は docs/14 §4.2、UE 版の `FacialPoseJson.cpp` に合わせる。

- 2 つの形式: `"FacialCorrection"`（全アセット → model.Document）、`"FacialPose"`（1 点 → model.PoseDocument）
- 読み込み
  - 知らないキーは `extra` に保持して書き戻す（往復で落とさない。トップレベルも入れ子の各オブジェクトも）
  - `version` が新しい（SUPPORTED_VERSION より大きい）ときは FcposeVersionWarning を出して読める所だけ読む
  - 欠けたキー・型が合わない値は既定値（UE 版と同じ寛容さ。厳密な検査は schema/fcpose.schema.json と validate が受け持つ）
  - クォータニオン [x, y, z, w] は正規化しない（往復で数値を変えない。正規化は呼ぶ側）
  - 格子の外の点も落とさず保持する（evaluate は見ない。UE 版は読むときに捨てるが、T-Drive は格子を広げ直せる）
  - FacialCorrection の layers が空 / 無いときは Neutral を 1 つ足す（UE 版と同じ）
- 書き出し
  - キーの順は固定（UE 版と同じ順 → T-Drive の追加キー → 知らないキー）。辞書の中身は持っている順
  - 数値: 整数と等しい浮動小数は整数で（`90` / `0`）、それ以外は最短で往復できる表記（repr）。NaN / Inf は書けない
  - 点は Layer.points にあるものだけ（行優先で並べる）。ベイクの状態は入れない
  - UTF-8（BOM なし）、改行 `\\n`、末尾に改行 1 つ。配列は数値だけなら 1 行
- UE 版が書いたファイルを読んで何も変えずに書くと、意味（JSON としての値）が同じになる（tests/facial/test_fcpose_io.py）
"""

from __future__ import annotations

import copy
import json
import math
import os
import warnings
from typing import Any, Optional, Union

from .model import (
    FORMAT_CORRECTION,
    FORMAT_POSE,
    SUPPORTED_VERSION,
    Autogen,
    Bake,
    BoneOffset,
    Document,
    Exclude,
    Grid,
    GridPoint,
    Layer,
    LipSync,
    LipSyncEntry,
    LipSyncVolume,
    LodMesh,
    Material,
    Meta,
    Mirror,
    PartStrength,
    Perspective,
    PerspectiveKey,
    Policy,
    PoseDocument,
    Quality,
    SculptShapes,
    SourcePose,
    Target,
    WorkingSet,
)

AnyDocument = Union[Document, PoseDocument]


class FcposeError(ValueError):
    """読めない（JSON が壊れている・format が違う）。"""


class FcposeVersionWarning(UserWarning):
    """version がこの実装より新しい。読める所だけ読んだ。"""


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _f(v: Any, default: float) -> float:
    return float(v) if _is_num(v) else default


INT_MIN, INT_MAX = -(2**31), 2**31 - 1


def _i(v: Any, default: int) -> int:
    """整数にする（0 方向への切り捨て。範囲外は 32 ビット符号付き整数の端に収める。C# の ToInt と同じ）。"""
    if not _is_num(v):
        return default
    if isinstance(v, float) and v != v:
        return 0
    return max(INT_MIN, min(INT_MAX, int(v)))


def _s(v: Any, default: str) -> str:
    return v if isinstance(v, str) else default


def _b(v: Any, default: bool) -> bool:
    return v if isinstance(v, bool) else default


def _vec(v: Any, n: int, default: tuple) -> tuple:
    if isinstance(v, list) and len(v) >= n and all(_is_num(c) for c in v[:n]):
        return tuple(float(c) for c in v[:n])
    return default


def _strs(v: Any) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _obj(d: dict, key: str) -> Optional[dict]:
    v = d.get(key)
    return v if isinstance(v, dict) else None


def _extra(d: dict, known: tuple[str, ...]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in d.items() if k not in known}


_META_KEYS = ("unit", "upAxis", "handedness", "source")


def _read_meta(d: Optional[dict]) -> Meta:
    if d is None:
        return Meta()
    m = Meta()
    return Meta(
        unit=_s(d.get("unit"), m.unit),
        up_axis=_s(d.get("upAxis"), m.up_axis),
        handedness=_s(d.get("handedness"), m.handedness),
        source=_s(d.get("source"), m.source),
        extra=_extra(d, _META_KEYS),
    )


_BONE_KEYS = ("t", "r", "s")


def _read_bone(d: dict) -> BoneOffset:
    b = BoneOffset()
    return BoneOffset(
        t=_vec(d.get("t"), 3, b.t),
        r=_vec(d.get("r"), 4, b.r),
        s=_vec(d.get("s"), 3, b.s),
        extra=_extra(d, _BONE_KEYS),
    )


def _read_pose(curves: Any, bones: Any) -> SourcePose:
    pose = SourcePose()
    if isinstance(curves, dict):
        pose.curves = {k: float(v) for k, v in curves.items() if _is_num(v)}
        if len(pose.curves) != len(curves):
            warnings.warn(f"数値でない値のシェイプ {len(curves) - len(pose.curves)} 個を読み飛ばしました", UserWarning, stacklevel=4)
    if isinstance(bones, dict):
        pose.bones = {k: _read_bone(v) for k, v in bones.items() if isinstance(v, dict)}
        if len(pose.bones) != len(bones):
            warnings.warn(f"オブジェクトでない値のボーン {len(bones) - len(pose.bones)} 個を読み飛ばしました", UserWarning, stacklevel=4)
    return pose


_POINT_KEYS = ("row", "col", "isKey", "curves", "bones")


def _read_point(d: dict) -> Optional[GridPoint]:
    if not (_is_num(d.get("row")) and _is_num(d.get("col"))):
        warnings.warn("row / col が無い点を読み飛ばしました", UserWarning, stacklevel=4)
        return None
    return GridPoint(
        row=_i(d["row"], 0),
        col=_i(d["col"], 0),
        is_key=_b(d.get("isKey"), False),
        pose=_read_pose(d.get("curves"), d.get("bones")),
        extra=_extra(d, _POINT_KEYS),
    )


_LAYER_KEYS = ("name", "emotionCurve", "enabled", "points")


def _read_layer(d: dict) -> Layer:
    layer = Layer(
        name=_s(d.get("name"), "Neutral"),
        emotion_curve=_s(d.get("emotionCurve"), ""),
        enabled=_b(d.get("enabled"), True),
        extra=_extra(d, _LAYER_KEYS),
    )
    points = d.get("points")
    if isinstance(points, list):
        for p in points:
            if isinstance(p, dict):
                point = _read_point(p)
                if point is not None:
                    if (point.row, point.col) in layer.points:
                        warnings.warn(
                            f"レイヤー「{layer.name}」に同じ位置の点が重複しています（R{point.row} C{point.col}）。後のものを使います", UserWarning, stacklevel=3
                        )
                    layer.points[(point.row, point.col)] = point
    return layer


_GRID_KEYS = ("yawRange", "pitchRange", "cols", "rows", "baseBone", "forwardAxis", "centerOffset", "edgeFade")


def _read_grid(d: Optional[dict]) -> Grid:
    g = Grid()
    if d is None:
        return g
    return Grid(
        yaw_range=_f(d.get("yawRange"), g.yaw_range),
        pitch_range=_f(d.get("pitchRange"), g.pitch_range),
        cols=_i(d.get("cols"), g.cols),
        rows=_i(d.get("rows"), g.rows),
        base_bone=_s(d.get("baseBone"), g.base_bone),
        forward_axis=_s(d.get("forwardAxis"), g.forward_axis),
        center_offset=_vec(d.get("centerOffset"), 3, g.center_offset),
        edge_fade=_f(d.get("edgeFade"), g.edge_fade),
        extra=_extra(d, _GRID_KEYS),
    )


_POLICY_KEYS = ("expressionDampen", "interpSpeed", "snapAngle", "fade", "globalAlpha")


def _read_policy(d: Optional[dict]) -> Policy:
    p = Policy()
    if d is None:
        return p
    return Policy(
        expression_dampen=_f(d.get("expressionDampen"), p.expression_dampen),
        interp_speed=_f(d.get("interpSpeed"), p.interp_speed),
        snap_angle=_f(d.get("snapAngle"), p.snap_angle),
        fade=_vec(d.get("fade"), 2, p.fade),
        global_alpha=_f(d.get("globalAlpha"), p.global_alpha),
        extra=_extra(d, _POLICY_KEYS),
    )


def _read_working_set(d: Optional[dict]) -> WorkingSet:
    if d is None:
        return WorkingSet()
    return WorkingSet(curves=_strs(d.get("curves")), bones=_strs(d.get("bones")), extra=_extra(d, ("curves", "bones")))


_MIRROR_KEYS = ("enabled", "suffixL", "suffixR", "exclude", "boneAxis")


def _read_mirror(d: Optional[dict]) -> Mirror:
    m = Mirror()
    if d is None:
        return m
    return Mirror(
        enabled=_b(d.get("enabled"), m.enabled),
        suffix_l=_s(d.get("suffixL"), m.suffix_l),
        suffix_r=_s(d.get("suffixR"), m.suffix_r),
        exclude=_strs(d.get("exclude")),
        bone_axis=_s(d.get("boneAxis"), m.bone_axis),
        extra=_extra(d, _MIRROR_KEYS),
    )


def _read_autogen(d: Optional[dict]) -> Autogen:
    a = Autogen()
    if d is None:
        return a
    return Autogen(
        mode=_s(d.get("mode"), a.mode),
        idw_power=_f(d.get("idwPower"), a.idw_power),
        extra=_extra(d, ("mode", "idwPower")),
    )


def _read_exclude(d: Optional[dict]) -> Exclude:
    if d is None:
        return Exclude()
    return Exclude(curves=_strs(d.get("curves")), bones=_strs(d.get("bones")), extra=_extra(d, ("curves", "bones")))


def _read_lod_meshes(v: Any) -> list[LodMesh]:
    out: list[LodMesh] = []
    if isinstance(v, list):
        for e in v:
            if isinstance(e, dict) and isinstance(e.get("mesh"), str):
                out.append(LodMesh(mesh=e["mesh"], lod=_i(e.get("lod"), 1), extra=_extra(e, ("mesh", "lod"))))
    return out


def _read_target(d: dict) -> Target:
    return Target(
        mesh=_s(d.get("mesh"), ""),
        extra_meshes=_strs(d.get("extraMeshes")),
        lod_meshes=_read_lod_meshes(d.get("lodMeshes")),
        extra=_extra(d, ("mesh", "extraMeshes", "lodMeshes")),
    )


def _read_part_strength(v: Any) -> list[PartStrength]:
    out: list[PartStrength] = []
    if isinstance(v, list):
        for e in v:
            if isinstance(e, dict) and isinstance(e.get("pattern"), str):
                out.append(PartStrength(pattern=e["pattern"], strength=_f(e.get("strength"), 1.0), extra=_extra(e, ("pattern", "strength"))))
    return out


def _read_bake(d: dict) -> Bake:
    b = Bake()
    return Bake(
        delta_threshold=_f(d.get("deltaThreshold"), b.delta_threshold),
        differential=_b(d.get("differential"), b.differential),
        part_strength=_read_part_strength(d.get("partStrength")),
        extra=_extra(d, ("deltaThreshold", "differential", "partStrength")),
    )


def _read_material(d: dict) -> Material:
    return Material(mode=_s(d.get("mode"), "none"), extra=_extra(d, ("mode",)))


_QUALITY_KEYS = ("sharpness", "stepFps", "angleEpsilon", "maxLod", "exaggeration")


def _read_quality(d: dict) -> Quality:
    q = Quality()
    return Quality(
        sharpness=_f(d.get("sharpness"), q.sharpness),
        step_fps=_f(d.get("stepFps"), q.step_fps),
        angle_epsilon=_f(d.get("angleEpsilon"), q.angle_epsilon),
        max_lod=_i(d.get("maxLod"), q.max_lod),
        exaggeration=_f(d.get("exaggeration"), q.exaggeration),
        extra=_extra(d, _QUALITY_KEYS),
    )


_PERSP_KEY_KEYS = ("value", "curves", "bones")
_PERSP_KEYS = ("enabled", "axis", "strength", "keys")


def _read_perspective_key(d: Any) -> Optional[PerspectiveKey]:
    if not isinstance(d, dict):
        warnings.warn("オブジェクトでないパース補正のキーを読み飛ばしました", UserWarning, stacklevel=4)
        return None
    if not _is_num(d.get("value")):
        warnings.warn("value が数でないパース補正のキーを読み飛ばしました", UserWarning, stacklevel=4)
        return None
    pose = _read_pose(d.get("curves"), d.get("bones"))
    return PerspectiveKey(
        value=float(d["value"]), curves=pose.curves, bones=pose.bones, extra=_extra(d, _PERSP_KEY_KEYS)
    )


def _read_perspective(d: dict) -> Perspective:
    keys = d.get("keys")
    out = []
    if isinstance(keys, list):
        out = [k for k in (_read_perspective_key(x) for x in keys) if k is not None]
    return Perspective(
        enabled=_b(d.get("enabled"), False),
        axis=_s(d.get("axis"), "distance"),
        strength=_f(d.get("strength"), 1.0),
        keys=out,
        extra=_extra(d, _PERSP_KEYS),
    )


_LIP_ENTRY_KEYS = ("phoneme", "emotion", "curves")
_LIP_VOLUME_KEYS = ("min", "max", "from", "to")
_LIP_KEYS = ("enabled", "strength", "phonemes", "entries", "volume", "follow")


def _read_lip_entry(d: Any) -> Optional[LipSyncEntry]:
    if not isinstance(d, dict):
        warnings.warn("オブジェクトでないリップシンクの行を読み飛ばしました", UserWarning, stacklevel=4)
        return None
    if not isinstance(d.get("phoneme"), str):
        warnings.warn("phoneme が文字列でないリップシンクの行を読み飛ばしました", UserWarning, stacklevel=4)
        return None
    pose = _read_pose(d.get("curves"), None)
    return LipSyncEntry(
        phoneme=d["phoneme"], emotion=_s(d.get("emotion"), ""), curves=pose.curves, extra=_extra(d, _LIP_ENTRY_KEYS)
    )


def _read_lip_volume(d: Optional[dict]) -> LipSyncVolume:
    v = LipSyncVolume()
    if d is None:
        return v
    return LipSyncVolume(
        min=_f(d.get("min"), v.min),
        max=_f(d.get("max"), v.max),
        from_=_f(d.get("from"), v.from_),
        to=_f(d.get("to"), v.to),
        extra=_extra(d, _LIP_VOLUME_KEYS),
    )


def _read_lip_sync(d: dict) -> LipSync:
    entries = d.get("entries")
    out = []
    if isinstance(entries, list):
        out = [e for e in (_read_lip_entry(x) for x in entries) if e is not None]
    base = LipSync()
    return LipSync(
        enabled=_b(d.get("enabled"), True),
        strength=_f(d.get("strength"), base.strength),
        phonemes=_strs(d.get("phonemes")),
        entries=out,
        volume=_read_lip_volume(_obj(d, "volume")),
        follow=_f(d.get("follow"), base.follow),
        extra=_extra(d, _LIP_KEYS),
    )


def _read_limits(d: dict) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for name, v in d.items():
        rng = _vec(v, 2, None)  # type: ignore[arg-type]
        if rng is not None:
            out[name] = rng
    return out


def _read_layer_weights(d: dict) -> dict[str, dict[str, Any]]:
    return {k: copy.deepcopy(v) for k, v in d.items() if isinstance(v, dict)}


def _read_sculpt(d: dict) -> SculptShapes:
    return SculptShapes(prefix=_s(d.get("prefix"), "fcs_"), extra=_extra(d, ("prefix",)))


_DOC_KEYS = (
    "format", "version", "meta", "grid", "policy", "layers", "workingSet", "mirror", "autogen", "exclude",
    "intensityCurves", "profile", "asset", "target", "bake", "limits", "material", "quality", "perspective", "lipSync",
    "layerWeights", "sculptShapes",
)  # fmt: skip
_POSE_KEYS = ("format", "version", "meta", "curves", "bones")


def _check_version(d: dict) -> int:
    version = _i(d.get("version"), SUPPORTED_VERSION)
    if version > SUPPORTED_VERSION:
        warnings.warn(
            f"fcpose の version={version} はこの実装（{SUPPORTED_VERSION}）より新しいため、読める範囲だけ読みます",
            FcposeVersionWarning,
            stacklevel=3,
        )
    return version


def from_dict(d: dict) -> AnyDocument:
    """パース済みの JSON（dict）から Document / PoseDocument を作る。"""
    if not isinstance(d, dict):
        raise FcposeError("JSON のトップレベルがオブジェクトではありません")
    fmt = d.get("format")
    if fmt == FORMAT_CORRECTION:
        return _read_document(d)
    if fmt == FORMAT_POSE:
        return _read_pose_document(d)
    raise FcposeError(f"format が FacialCorrection / FacialPose ではありません: {fmt!r}")


def _read_document(d: dict) -> Document:
    version = _check_version(d)
    doc = Document(version=version, extra=_extra(d, _DOC_KEYS))
    doc.meta = _read_meta(_obj(d, "meta"))
    doc.grid = _read_grid(_obj(d, "grid"))
    doc.policy = _read_policy(_obj(d, "policy"))
    layers = d.get("layers")
    doc.layers = [_read_layer(x) for x in layers if isinstance(x, dict)] if isinstance(layers, list) else []
    if not doc.layers:
        doc.layers = [Layer()]  # 最低でも Neutral は存在させる（UE 版と同じ）
    doc.working_set = _read_working_set(_obj(d, "workingSet"))
    doc.mirror = _read_mirror(_obj(d, "mirror"))
    doc.autogen = _read_autogen(_obj(d, "autogen"))
    doc.exclude = _read_exclude(_obj(d, "exclude"))
    doc.intensity_curves = _strs(d.get("intensityCurves"))
    doc.profile = _s(d.get("profile"), "")
    # 追加キー
    if isinstance(d.get("asset"), str):
        doc.asset = d["asset"]
    for key, attr, reader in (
        ("target", "target", _read_target),
        ("bake", "bake", _read_bake),
        ("limits", "limits", _read_limits),
        ("material", "material", _read_material),
        ("quality", "quality", _read_quality),
        ("perspective", "perspective", _read_perspective),
        ("lipSync", "lip_sync", _read_lip_sync),
        ("layerWeights", "layer_weights", _read_layer_weights),
        ("sculptShapes", "sculpt_shapes", _read_sculpt),
    ):
        sub = _obj(d, key)
        if sub is not None:
            setattr(doc, attr, reader(sub))
    return doc


def _read_pose_document(d: dict) -> PoseDocument:
    version = _check_version(d)
    return PoseDocument(
        version=version,
        meta=_read_meta(_obj(d, "meta")),
        pose=_read_pose(d.get("curves"), d.get("bones")),
        extra=_extra(d, _POSE_KEYS),
    )


def loads(text: str) -> AnyDocument:
    """JSON 文字列 → Document / PoseDocument（format で決まる）。"""
    if text.startswith("﻿"):
        text = text[1:]
    return from_dict(parse_json(text))


def parse_json(text: str) -> Any:
    """厳密な JSON の読み込み（NaN / Infinity / 1e999・深すぎる入れ子・巨大な整数を FcposeError にする。C# の MiniJson と同じ）。"""
    try:
        d = json.loads(text, parse_constant=_reject_constant, parse_float=_parse_float, parse_int=_parse_int)
    except json.JSONDecodeError as e:
        raise FcposeError(f"JSON の解析に失敗しました（書式が壊れています）: {e}") from e
    except RecursionError as e:
        raise FcposeError(f"JSON の入れ子が深すぎます（上限 {MAX_DEPTH}）") from e
    _check_depth(d)
    return d


MAX_DEPTH = 256  # 入れ子の深さの上限（C# の MiniJson.MaxDepth と同じ。超えたら読み込みを拒否する）


def _reject_constant(token: str) -> float:
    """NaN / Infinity / -Infinity は受け付けない（C# の MiniJson も拒否する）。"""
    raise FcposeError(f"JSON に有限でない数（{token}）があります")


def _parse_float(token: str) -> float:
    v = float(token)
    if math.isinf(v) or math.isnan(v):
        raise FcposeError(f"JSON に有限でない数（{token}）があります")  # 1e999 など
    return v


def _parse_int(token: str) -> int:
    v = int(token)
    try:
        float(v)
    except OverflowError:
        raise FcposeError("JSON の整数が大きすぎます（double に収まりません）") from None
    return v


def _check_depth(root: Any) -> None:
    """オブジェクト / 配列の入れ子が MAX_DEPTH を超えていたら FcposeError（再帰を使わない）。"""
    stack = [(root, 1)]
    while stack:
        v, depth = stack.pop()
        if isinstance(v, dict):
            children = list(v.values())
        elif isinstance(v, list):
            children = v
        else:
            continue
        if depth > MAX_DEPTH:
            raise FcposeError(f"JSON の入れ子が深すぎます（上限 {MAX_DEPTH}）")
        stack.extend((c, depth + 1) for c in children)


def load(path: Union[str, os.PathLike]) -> AnyDocument:
    """ファイル → Document / PoseDocument。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        return loads(f.read())


def load_document(path: Union[str, os.PathLike]) -> Document:
    """FacialCorrection だけを読む（FacialPose は FcposeError）。"""
    doc = load(path)
    if not isinstance(doc, Document):
        raise FcposeError("FacialCorrection 形式ではありません（FacialPose です）")
    return doc


def load_pose(path: Union[str, os.PathLike]) -> PoseDocument:
    """FacialPose だけを読む（FacialCorrection は FcposeError）。"""
    doc = load(path)
    if not isinstance(doc, PoseDocument):
        raise FcposeError("FacialPose 形式ではありません（FacialCorrection です）")
    return doc


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def _with_extra(d: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for k, v in extra.items():
        if k not in d:
            d[k] = copy.deepcopy(v)
    return d


def _bone_dict(b: BoneOffset) -> dict[str, Any]:
    return _with_extra({"t": list(b.t), "r": list(b.r), "s": list(b.s)}, b.extra)


def _pose_parts(pose: SourcePose) -> tuple[dict[str, Any], dict[str, Any]]:
    return dict(pose.curves), {name: _bone_dict(b) for name, b in pose.bones.items()}


def _meta_dict(m: Meta) -> dict[str, Any]:
    return _with_extra({"unit": m.unit, "upAxis": m.up_axis, "handedness": m.handedness, "source": m.source}, m.extra)


def _point_dict(p: GridPoint) -> dict[str, Any]:
    curves, bones = _pose_parts(p.pose)
    return _with_extra({"row": p.row, "col": p.col, "isKey": p.is_key, "curves": curves, "bones": bones}, p.extra)


def _layer_dict(layer: Layer) -> dict[str, Any]:
    points = [_point_dict(layer.points[k]) for k in sorted(layer.points)]
    return _with_extra(
        {"name": layer.name, "emotionCurve": layer.emotion_curve, "enabled": layer.enabled, "points": points},
        layer.extra,
    )


def to_dict(doc: AnyDocument) -> dict[str, Any]:
    """Document / PoseDocument → JSON の dict（キーの順は固定）。"""
    if isinstance(doc, PoseDocument):
        curves, bones = _pose_parts(doc.pose)
        return _with_extra(
            {
                "format": FORMAT_POSE,
                "version": doc.version,
                "meta": _meta_dict(doc.meta),
                "curves": curves,
                "bones": bones,
            },
            doc.extra,
        )
    g, p = doc.grid, doc.policy
    out: dict[str, Any] = {
        "format": FORMAT_CORRECTION,
        "version": doc.version,
        "meta": _meta_dict(doc.meta),
        "grid": _with_extra(
            {
                "yawRange": g.yaw_range,
                "pitchRange": g.pitch_range,
                "cols": g.cols,
                "rows": g.rows,
                "baseBone": g.base_bone,
                "forwardAxis": g.forward_axis,
                "centerOffset": list(g.center_offset),
                "edgeFade": g.edge_fade,
            },
            g.extra,
        ),
        "policy": _with_extra(
            {
                "expressionDampen": p.expression_dampen,
                "interpSpeed": p.interp_speed,
                "snapAngle": p.snap_angle,
                "fade": list(p.fade),
                "globalAlpha": p.global_alpha,
            },
            p.extra,
        ),
        "layers": [_layer_dict(layer) for layer in doc.layers],
        "workingSet": _with_extra(
            {"curves": list(doc.working_set.curves), "bones": list(doc.working_set.bones)}, doc.working_set.extra
        ),
        "mirror": _with_extra(
            {
                "enabled": doc.mirror.enabled,
                "suffixL": doc.mirror.suffix_l,
                "suffixR": doc.mirror.suffix_r,
                "exclude": list(doc.mirror.exclude),
                "boneAxis": doc.mirror.bone_axis,
            },
            doc.mirror.extra,
        ),
        "autogen": _with_extra({"mode": doc.autogen.mode, "idwPower": doc.autogen.idw_power}, doc.autogen.extra),
        "exclude": _with_extra(
            {"curves": list(doc.exclude.curves), "bones": list(doc.exclude.bones)}, doc.exclude.extra
        ),
        "intensityCurves": list(doc.intensity_curves),
        "profile": doc.profile,
    }
    # T-Drive の追加キー（None は出さない）
    if doc.asset is not None:
        out["asset"] = doc.asset
    if doc.target is not None:
        td: dict[str, Any] = {"mesh": doc.target.mesh, "extraMeshes": list(doc.target.extra_meshes)}
        if doc.target.lod_meshes:  # 無いときは出さない（既存のファイルを変えない）
            td["lodMeshes"] = [_with_extra({"mesh": m.mesh, "lod": m.lod}, m.extra) for m in doc.target.lod_meshes]
        out["target"] = _with_extra(td, doc.target.extra)
    if doc.bake is not None:
        bd: dict[str, Any] = {"deltaThreshold": doc.bake.delta_threshold, "differential": doc.bake.differential}
        if doc.bake.part_strength:
            bd["partStrength"] = [_with_extra({"pattern": e.pattern, "strength": e.strength}, e.extra) for e in doc.bake.part_strength]
        out["bake"] = _with_extra(bd, doc.bake.extra)
    if doc.limits is not None:
        out["limits"] = {k: list(v) for k, v in doc.limits.items()}
    if doc.material is not None:
        out["material"] = _with_extra({"mode": doc.material.mode}, doc.material.extra)
    if doc.quality is not None:
        q = doc.quality
        qd = {"sharpness": q.sharpness, "stepFps": q.step_fps, "angleEpsilon": q.angle_epsilon, "maxLod": q.max_lod}
        if q.exaggeration != 1.0:
            qd["exaggeration"] = q.exaggeration  # 既定（1）のときは出さない（既存のファイルを変えない）
        out["quality"] = _with_extra(qd, q.extra)
    if doc.perspective is not None:
        out["perspective"] = _perspective_dict(doc.perspective)
    if doc.lip_sync is not None:
        out["lipSync"] = _lip_sync_dict(doc.lip_sync)
    if doc.layer_weights is not None:
        out["layerWeights"] = copy.deepcopy(doc.layer_weights)
    if doc.sculpt_shapes is not None:
        out["sculptShapes"] = _with_extra({"prefix": doc.sculpt_shapes.prefix}, doc.sculpt_shapes.extra)
    return _with_extra(out, doc.extra)


def _perspective_dict(p: Perspective) -> dict[str, Any]:
    d: dict[str, Any] = {"enabled": p.enabled}
    if p.axis != "distance":
        d["axis"] = p.axis  # 既定（distance）のときは出さない（既存のファイルを変えない）
    if p.strength != 1.0:
        d["strength"] = p.strength
    keys = []
    for k in p.keys:
        curves, bones = _pose_parts(k.pose)
        keys.append(_with_extra({"value": k.value, "curves": curves, "bones": bones}, k.extra))
    d["keys"] = keys
    return _with_extra(d, p.extra)


def _lip_sync_dict(l: LipSync) -> dict[str, Any]:
    entries = [
        _with_extra({"phoneme": e.phoneme, "emotion": e.emotion, "curves": dict(e.curves)}, e.extra) for e in l.entries
    ]
    vol = _with_extra(
        {"min": l.volume.min, "max": l.volume.max, "from": l.volume.from_, "to": l.volume.to}, l.volume.extra
    )
    d = {
        "enabled": l.enabled,
        "strength": l.strength,
        "phonemes": list(l.phonemes),
        "entries": entries,
        "volume": vol,
        "follow": l.follow,
    }
    return _with_extra(d, l.extra)


def _fmt_number(v: Union[int, float]) -> str:
    if isinstance(v, int):
        return str(v)
    if not math.isfinite(v):
        raise ValueError(f"JSON に書けない数値: {v}")
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))  # -0.0 も "0"
    return repr(v)


def _is_scalar(v: Any) -> bool:
    return not isinstance(v, (dict, list, tuple))


def _dump(v: Any, level: int, out: list[str]) -> None:
    pad = "  " * (level + 1)
    end = "  " * level
    if isinstance(v, dict):
        if not v:
            out.append("{}")
            return
        out.append("{\n")
        items = list(v.items())
        for i, (k, val) in enumerate(items):
            out.append(f"{pad}{json.dumps(k, ensure_ascii=False)}: ")
            _dump(val, level + 1, out)
            out.append(",\n" if i + 1 < len(items) else "\n")
        out.append(f"{end}}}")
    elif isinstance(v, (list, tuple)):
        if not v:
            out.append("[]")
        elif all(_is_scalar(x) for x in v):
            out.append("[" + ", ".join(_scalar(x) for x in v) + "]")
        else:
            out.append("[\n")
            for i, val in enumerate(v):
                out.append(pad)
                _dump(val, level + 1, out)
                out.append(",\n" if i + 1 < len(v) else "\n")
            out.append(f"{end}]")
    else:
        out.append(_scalar(v))


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _fmt_number(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    raise TypeError(f"JSON に書けない値: {type(v).__name__}")


def dumps(doc: AnyDocument) -> str:
    """Document / PoseDocument → JSON 文字列（キー順・数値表記は固定。末尾に改行 1 つ）。"""
    out: list[str] = []
    _dump(to_dict(doc), 0, out)
    return "".join(out) + "\n"


def save(doc: AnyDocument, path: Union[str, os.PathLike]) -> None:
    """ファイルへ書く（UTF-8・BOM なし・改行 \\n）。親フォルダが無ければ作る。"""
    text = dumps(doc)  # 書けない値（NaN など）はここで ValueError。ファイルには触れない
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    # 一時ファイルに書いて置き換える（途中で失敗しても、唯一のファイルを壊さない。M-13）
    tmp = f"{os.fspath(path)}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
