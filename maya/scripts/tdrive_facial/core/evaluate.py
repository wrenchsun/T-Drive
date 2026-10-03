"""角度 → 補正シェイプの重みの計算（UE 版 `FacialCore.cpp` の写し。Maya 非依存）。

- 入出力は素のデータ（GridShape / LayerEvalInput / MorphWeight）。Document は受けない
- 計算は**正準空間**（UE 準拠: cm / Z-up / 左手 / 前 +X）で定義する。各環境の座標は space.py で入口に変換する
- UE 版の文書とコードが食い違う所はコードを正とする:
  感情レイヤーは Neutral を常に全量（双線形の重み 1.0）で足し、感情レイヤー（Neutral との差分で焼いてある）を
  感情の重みを掛けて上乗せする（`1 - Σw` で Neutral を減らす旧方式ではない）
- UE 版は float32。ここは float64 で計算する（共通のテストデータの許容誤差は重み 1e-4）
- 追加機能（シャープニング・コマ打ち・パース・誇張）は F5 で「既定値 = 何もしない」の引数として足す。
  既定のまま結果が変わってはいけない（共通テストデータが MAJOR の境界）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

KINDA_SMALL_NUMBER = 1e-4  # UE の KINDA_SMALL_NUMBER
SMALL_NUMBER = 1e-8  # UE の UE_SMALL_NUMBER（FMath::IsNearlyZero の既定の許容）


@dataclass
class GridShape:
    """格子の形（角度 → セル位置に必要な最小情報）。"""

    yaw_range_deg: float = 90.0
    pitch_range_deg: float = 45.0
    num_cols: int = 5
    num_rows: int = 3
    edge_fade_deg: float = 15.0  # 範囲の外側でこの幅をかけて 0 へ減衰（0 = 範囲外は即 0）


@dataclass
class LayerEvalInput:
    """1 レイヤー分の評価入力。corner_morph_names は行優先（index = row * num_cols + col）で、
    焼いていない点は None（または ""）= 重み 0 として飛ばす（フェイルソフト）。
    emotion_weight は Layers[0]（Neutral）では無視される（常に 1）。"""

    corner_morph_names: Sequence[Optional[str]] = field(default_factory=list)
    emotion_weight: float = 0.0
    enabled: bool = True


@dataclass
class MorphWeight:
    morph_name: str
    weight: float = 0.0


@dataclass
class GridCellInfo:
    """今の角度が属するセル（デバッグ表示用）。"""

    row0: int = 0
    col0: int = 0
    row1: int = 0
    col1: int = 0
    row_frac: float = 0.0
    col_frac: float = 0.0
    fade_scale: float = 1.0


@dataclass
class _AxisSample:
    index0: int = 0
    index1: int = 0
    frac: float = 0.0
    fade: float = 1.0


def is_nearly_zero(value: float, tolerance: float = SMALL_NUMBER) -> bool:
    return abs(value) <= tolerance


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def clamp_axis(angle: float) -> float:
    """FRotator::ClampAxis: [0, 360) に収める。"""
    angle = math.fmod(angle, 360.0)
    if angle < 0.0:
        angle += 360.0
    return angle


def normalize_axis(angle: float) -> float:
    """FRotator::NormalizeAxis: (-180, 180] に収める。"""
    angle = clamp_axis(angle)
    if angle > 180.0:
        angle -= 360.0
    return angle


def _sample_axis(angle_deg: float, range_deg: float, num_points: int, edge_fade_deg: float) -> _AxisSample:
    out = _AxisSample()
    if num_points <= 1:
        return out  # 単一点は常にその点を 100%
    safe_range = max(range_deg, KINDA_SMALL_NUMBER)
    u = (angle_deg / safe_range + 1.0) * 0.5  # 0..1（範囲外は外側へ延長）
    pos = clamp(u, 0.0, 1.0) * (num_points - 1)
    out.index0 = int(clamp(math.floor(pos), 0, num_points - 1))
    out.index1 = min(out.index0 + 1, num_points - 1)
    out.frac = pos - out.index0
    excess = max(0.0, abs(angle_deg) - range_deg)
    if excess <= 0.0:
        out.fade = 1.0
    elif edge_fade_deg <= 0.0:
        out.fade = 0.0
    else:
        out.fade = clamp(1.0 - excess / edge_fade_deg, 0.0, 1.0)
    return out


def evaluate_correction(
    grid: GridShape,
    layers: Sequence[LayerEvalInput],
    yaw_deg: float,
    pitch_deg: float,
) -> list[MorphWeight]:
    """角度（基準ボーンから見た Yaw / Pitch、度）から補正シェイプの重み一式を返す（EvaluateCorrection）。

    layers[0] は Neutral（基底。常に全量）。それ以外は Neutral との差分で焼いた感情レイヤーで、
    emotion_weight を掛けて足す。範囲の外は edge_fade_deg の幅で 0 へ減衰。
    無効なレイヤー・重み 0・焼いていない点は飛ばす。戻りは最初に現れた順。
    """
    if len(layers) == 0 or grid.num_cols <= 0 or grid.num_rows <= 0:
        return []
    col = _sample_axis(yaw_deg, grid.yaw_range_deg, grid.num_cols, grid.edge_fade_deg)
    row = _sample_axis(pitch_deg, grid.pitch_range_deg, grid.num_rows, grid.edge_fade_deg)
    fade_scale = col.fade * row.fade
    if fade_scale <= KINDA_SMALL_NUMBER:
        return []  # 範囲の外（フェード幅も超えた）

    corners = (
        (row.index0, col.index0, (1.0 - col.frac) * (1.0 - row.frac)),
        (row.index0, col.index1, col.frac * (1.0 - row.frac)),
        (row.index1, col.index0, (1.0 - col.frac) * row.frac),
        (row.index1, col.index1, col.frac * row.frac),
    )
    out: list[MorphWeight] = []
    index_of: dict[str, int] = {}
    for layer_index, layer in enumerate(layers):
        if not layer.enabled:
            continue
        layer_scale = 1.0 if layer_index == 0 else layer.emotion_weight
        if is_nearly_zero(layer_scale):
            continue
        names = layer.corner_morph_names
        for c_row, c_col, bilinear in corners:
            weight = bilinear * layer_scale * fade_scale
            if is_nearly_zero(weight):
                continue
            point_index = c_row * grid.num_cols + c_col
            if not 0 <= point_index < len(names):
                continue  # フェイルソフト: レイヤー間で格子の大きさが合わなくても 0 扱い
            name = names[point_index]
            if not name:
                continue  # 焼いていない点は 0 扱い
            at = index_of.get(name)
            if at is None:
                index_of[name] = len(out)
                out.append(MorphWeight(name, weight))
            else:
                out[at].weight += weight
    # 打ち消し合って 0 近傍になった分は除く
    return [w for w in out if not is_nearly_zero(w.weight)]


def compute_grid_cell(grid: GridShape, yaw_deg: float, pitch_deg: float) -> GridCellInfo:
    """今の角度が属するセル（4 隅の番号・補間係数・端のフェード）。"""
    if grid.num_cols <= 0 or grid.num_rows <= 0:
        return GridCellInfo()
    col = _sample_axis(yaw_deg, grid.yaw_range_deg, grid.num_cols, grid.edge_fade_deg)
    row = _sample_axis(pitch_deg, grid.pitch_range_deg, grid.num_rows, grid.edge_fade_deg)
    return GridCellInfo(row.index0, col.index0, row.index1, col.index1, row.frac, col.frac, col.fade * row.fade)


def point_angles(
    yaw_range_deg: float, pitch_range_deg: float, num_cols: int, num_rows: int, row: int, col: int
) -> tuple[float, float]:
    """格子の点 (row, col) の (Yaw, Pitch)。行 0 が -Pitch（あおり）、中央が正面。1 点しか無い軸は 0°。"""
    u = col / (num_cols - 1) if num_cols > 1 else 0.5
    v = row / (num_rows - 1) if num_rows > 1 else 0.5
    return (u * 2.0 - 1.0) * yaw_range_deg, (v * 2.0 - 1.0) * pitch_range_deg


def forward_axis_yaw_offset_deg(axis: str) -> float:
    """forwardAxis → 基準ボーンのワールド Yaw に足す角度（正準空間。GetForwardAxisYawOffsetDeg）。
    正準空間の前方軸 ±X / ±Y のみ（±Z は space.forward_axis_to_canonical で変換してから渡す）。"""
    try:
        return {"+X": 0.0, "-X": 180.0, "+Y": 90.0, "-Y": -90.0}[axis]
    except KeyError:
        raise ValueError(f"正準空間で扱えない forwardAxis: {axis!r}（±X / ±Y のみ）") from None


def compute_view_angles(
    head_pos: Sequence[float], head_forward_yaw_deg: float, viewer_pos: Sequence[float]
) -> tuple[float, float]:
    """基準ボーン（格子の中心 = centerOffset 適用済みのワールド位置）から見た視点の (Yaw, Pitch)（度）。

    head_forward_yaw_deg: 「キャラクターの前方」がワールドで向いている Yaw
    （= ボーンのワールド Yaw + forward_axis_yaw_offset_deg）。
    ふかん（視点が上）= Pitch 正。Yaw 正 = 対面したとき画面右側（キャラクターの左手側）へ回り込む向き。
    head_pos / viewer_pos は正準空間（Z-up、前 +X）。
    """
    dx = viewer_pos[0] - head_pos[0]
    dy = viewer_pos[1] - head_pos[1]
    dz = viewer_pos[2] - head_pos[2]
    horizontal = math.sqrt(dx * dx + dy * dy)
    cam_angle = math.degrees(math.atan2(dy, dx))
    yaw = normalize_axis(head_forward_yaw_deg - cam_angle)
    pitch = math.degrees(math.atan2(dz, horizontal))
    return yaw, pitch


def compute_view_direction(head_forward_yaw_deg: float, yaw_deg: float, pitch_deg: float) -> tuple[float, float, float]:
    """compute_view_angles の逆写像。基準位置から視点へ向かう単位ベクトル（正準空間）。"""
    cam = math.radians(head_forward_yaw_deg - yaw_deg)
    pitch = math.radians(pitch_deg)
    return (
        math.cos(cam) * math.cos(pitch),
        math.sin(cam) * math.cos(pitch),
        math.sin(pitch),
    )


def should_snap(
    prev_angles: Optional[tuple[float, float]], yaw_deg: float, pitch_deg: float, snap_angle_deg: float
) -> bool:
    """カット切り替え判定（UE 版 Component / AnimNode と同じ）。前回の角度が無い、または
    Yaw（正規化した差）か Pitch の変化が snap_angle_deg を超えたらスナップ。"""
    if prev_angles is None:
        return True
    return (
        abs(normalize_axis(yaw_deg - prev_angles[0])) > snap_angle_deg
        or abs(pitch_deg - prev_angles[1]) > snap_angle_deg
    )


def expression_scale(expression_dampen: float, intensity_s: float) -> float:
    """表情が強いときに補正を弱める: 1 - dampen × s（どちらも 0〜1 に丸める）。"""
    return 1.0 - clamp(expression_dampen, 0.0, 1.0) * clamp(intensity_s, 0.0, 1.0)


def distance_fade(distance: float, fade_start: float, fade_end: float) -> float:
    """視点までの距離フェード。end <= start は無効（常に 1）。start〜end で線形に 1 → 0。"""
    if fade_end <= fade_start:
        return 1.0
    if distance <= fade_start:
        return 1.0
    if distance >= fade_end:
        return 0.0
    return 1.0 - (distance - fade_start) / (fade_end - fade_start)


def finterp_to(current: float, target: float, delta_time: float, interp_speed: float) -> float:
    """UE の FMath::FInterpTo と同じ。"""
    if interp_speed <= 0.0:
        return target
    dist = target - current
    if dist * dist < SMALL_NUMBER:
        return target
    delta_move = dist * clamp(delta_time * interp_speed, 0.0, 1.0)
    return current + delta_move


def smooth_weights(
    prev: Iterable[MorphWeight],
    target: Iterable[MorphWeight],
    delta_time: float,
    interp_speed: float,
    snap: bool,
) -> list[MorphWeight]:
    """重みの指数補間（SmoothWeights）。interp_speed <= 0 または snap なら target をそのまま返す。
    前回にだけある名前は 0 へ向かって補間し、ほぼ 0 になるまで出力に残す。
    戻りは「前回の順（残るもの）→ 今回新しく出てきたもの」の順。"""
    prev = list(prev)
    target = list(target)
    target_by_name: dict[str, float] = {}
    for w in target:
        target_by_name.setdefault(w.morph_name, w.weight)  # 同名は先勝ち（UE 版の線形探索と同じ）
    prev_names = {w.morph_name for w in prev}
    instant = snap or interp_speed <= 0.0
    out: list[MorphWeight] = []
    for p in prev:
        has_target = p.morph_name in target_by_name
        tv = target_by_name.get(p.morph_name, 0.0)
        new_value = tv if instant else finterp_to(p.weight, tv, delta_time, interp_speed)
        if has_target or not is_nearly_zero(new_value):
            out.append(MorphWeight(p.morph_name, new_value))
    for t in target:
        if t.morph_name in prev_names:
            continue
        new_value = t.weight if instant else finterp_to(0.0, t.weight, delta_time, interp_speed)
        out.append(MorphWeight(t.morph_name, new_value))
    return out
