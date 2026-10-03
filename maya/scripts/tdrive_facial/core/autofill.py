"""自動生成・格子サイズ変更・データ間のコピー（UE 版 `FacialCorrectionAsset.cpp` の GenerateFromKeys /
InterpolatePoseAtAngles / MirrorBoneOffset と `FacialCorrectionCopier.cpp` の写し。Maya 非依存）。

- 入出力は core/model.py のデータクラス。計算は文書の座標系のまま行う（正準空間へ変換してから生成して戻した結果と同じ）:
  ボーンのずらしの鏡映は `doc.mirror.bone_axis`（文書の系の軸名）で反射するので、変換と順序を入れ替えても結果は変わらない。
  ボーンの平行移動を捨てるしきい値（UE は 1e-3 cm）だけは単位に依存するため、文書の単位（meta.unit）で換算する
- UE 版の文書とコードが食い違う所はコードを正とする。UE 版は float32、ここは float64 で計算する
  （共通のテストデータの許容誤差は 1e-4）

UE 版の規則（本ファイルの各関数が写している所）:
- キーを集める → ミラーがオンなら yaw が 0.1° より大きいキーを −Yaw 側へ複製（その位置の 1° 以内に実キー・先に作った仮想キーが
  あれば作らない）。複製はカーブ名とボーン名の L/R 接尾辞を入れ替え、除外パターンに部分一致する名前は複製しない
  （カーブもボーンも同じパターン）。ボーンは変位を鏡映（`mirror_pose`）
- 非キー点を IDW（距離を yaw / pitch の範囲で割って正規化、重み 1/d^power、d < 1e-4 は完全一致としてそのキーをコピー）か
  NearestKey（度のまま二乗距離が最小のキー。同点は先のキー）で埋める
- IDW の結果: カーブは重み付き和で、絶対値が 1e-3 以下のものは捨てる。ボーンは平行移動が重み付き和、回転は最初に見つけた
  クォータニオンと向きをそろえた重み付き和を正規化。**スケールは補間しない**（結果は常に 1。UE は `FTransform(Rotation, Translation)`
  で作るため）。平行移動が 1e-3 cm 以下かつ回転が単位（各成分 1e-4 以内）のボーンは捨てる。完全一致・NearestKey は
  キーのポーズをそのままコピーするので、その場合だけスケールも残る。ミラーの仮想キーのスケールは 1
- 自動生成した点は `is_key=False`。キーは触らない。結果が空になった非キー点は点ごと消す（UE の `bAuthored=false`）
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Union

from . import space as _space
from .evaluate import KINDA_SMALL_NUMBER, point_angles
from .model import (
    FILL_MODES,
    MAX_LAYERS,
    BoneOffset,
    Document,
    GridPoint,
    Layer,
    Mirror,
    Quat,
    SourcePose,
    Vec3,
)

CURVE_DROP_THRESHOLD = 1e-3  # この値以下（絶対値）の補間結果のカーブは捨てる
BONE_TRANSLATION_DROP_CM = 1e-3  # 平行移動がこの値以下（cm）かつ回転が単位なら捨てる
BONE_ROTATION_IDENTITY_TOL = 1e-4  # 単位クォータニオンとみなす許容
MIRROR_YAW_SKIP_DEG = 0.1  # |yaw| がこれ以下のキーは正面列として複製しない
MIRROR_OCCUPIED_DEG = 1.0  # 反転位置からこの距離（度）未満に既存のキーがあれば複製しない
QUAT_NORMALIZE_TOL = 1e-8  # FQuat::Normalize の既定（二乗和がこれ未満なら単位へ）
RESIZE_KEY_MATCH_DEG = 1e-3  # 格子サイズ変更で「同じ角度」とみなす距離（度）


@dataclass
class KeySample:
    """角度付きのキー 1 点（UE の FFacialKeySample）。yaw / pitch は度。"""

    yaw: float
    pitch: float
    pose: SourcePose


@dataclass
class AutofillSummary:
    """自動生成の集計（全対象レイヤーの合計）。"""

    layers: int = 0  # キーがあって処理したレイヤー数
    keys: int = 0  # 実キーの数
    mirror_keys: int = 0  # 足した仮想キーの数
    generated: int = 0  # 書き込んだ自動生成の点の数（空になって消えた点は含まない）
    cleared: int = 0  # 結果が空になり、点を消した数
    skipped_layers: list[str] = field(default_factory=list)  # キーが無くて何もしなかったレイヤー名


@dataclass
class DroppedKey:
    """格子サイズ変更で、新しい格子のどの点にも重ならず捨てたキー。"""

    layer: str
    row: int  # 旧格子での位置
    col: int
    yaw: float
    pitch: float


@dataclass
class ResizeSummary:
    kept_keys: int = 0  # 角度が新しい格子の点と重なり、キーのまま残った数
    resampled: int = 0  # 実キーから再サンプルして作った非キー点の数
    dropped_keys: list[DroppedKey] = field(default_factory=list)  # UI が警告に使う


@dataclass
class CopyReport:
    copied_layers: int = 0
    copied_points: int = 0
    created_layers: list[str] = field(default_factory=list)  # 宛先に無くて新規作成したレイヤー名
    skipped_layers: list[str] = field(default_factory=list)  # サンプルが無く何もしなかった（ソース側の）レイヤー名
    layer_limit_reached: list[str] = field(default_factory=list)  # 上限（MAX_LAYERS）で作れなかったレイヤー名


# --------------------------------------------------------------------------------------
# 名前・ボーンの鏡映
# --------------------------------------------------------------------------------------


def mirror_name(name: str, mirror: Mirror) -> str:
    """末尾の L/R 接尾辞を入れ替える（MirrorCurveName）。どちらかの接尾辞が空なら何もしない。"""
    left, right = mirror.suffix_l, mirror.suffix_r
    if not left or not right:
        return name
    if name.endswith(left):
        return name[: len(name) - len(left)] + right
    if name.endswith(right):
        return name[: len(name) - len(right)] + left
    return name


def is_mirror_excluded(name: str, mirror: Mirror) -> bool:
    """除外パターン（空でないもの）が名前に部分一致（大文字小文字を区別）するか（IsMirrorExcluded）。"""
    return any(p and p in name for p in mirror.exclude)


def _quat_normalize(q: Sequence[float]) -> Quat:
    sq = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]
    if sq >= QUAT_NORMALIZE_TOL:
        inv = 1.0 / math.sqrt(sq)
        return (q[0] * inv, q[1] * inv, q[2] * inv, q[3] * inv)
    return (0.0, 0.0, 0.0, 1.0)


def mirror_bone_offset(offset: BoneOffset, axis: str) -> BoneOffset:
    """対称軸（文書の系の軸名 "X" "Y" "Z"）に垂直な平面で反射する（MirrorBoneOffset）。
    平行移動は軸成分を反転。回転は軸まわりの成分を残し、他の 2 成分を反転して正規化。スケールは 1 に戻る（UE 準拠）。"""
    t = list(offset.t)
    x, y, z, w = offset.r
    if axis == "X":
        t[0] = -t[0]
        r = (x, -y, -z, w)
    elif axis == "Y":
        t[1] = -t[1]
        r = (-x, y, -z, w)
    elif axis == "Z":
        t[2] = -t[2]
        r = (-x, -y, z, w)
    else:
        raise ValueError(f"未知の mirror.boneAxis: {axis!r}")
    return BoneOffset(t=(t[0], t[1], t[2]), r=_quat_normalize(r))


def mirror_pose(pose: SourcePose, mirror: Mirror) -> SourcePose:
    """1 点のポーズを左右反転した複製を返す（UI の「左右反転」、仮想キーの作成）。
    除外パターンに一致する名前は結果に含めない。`mirror.enabled` は見ない（呼ぶ側が決める）。
    入替の結果が同名になる 2 つの入力（接尾辞が片方だけに付く名前など）は、後に書いた方が残る。"""
    out = SourcePose()
    for name, value in pose.curves.items():
        if is_mirror_excluded(name, mirror):
            continue
        out.curves[mirror_name(name, mirror)] = value
    for name, offset in pose.bones.items():
        if is_mirror_excluded(name, mirror):
            continue
        out.bones[mirror_name(name, mirror)] = mirror_bone_offset(offset, mirror.bone_axis)
    return out


# --------------------------------------------------------------------------------------
# キーの収集・補間
# --------------------------------------------------------------------------------------


def _check_layer(doc: Document, layer_index: int) -> Layer:
    if not 0 <= layer_index < len(doc.layers):
        raise IndexError(f"レイヤー番号が範囲外: {layer_index}")
    return doc.layers[layer_index]


def collect_keys(doc: Document, layer: Layer) -> list[KeySample]:
    """レイヤーの実キー（格子の中の is_key=True の点）を (row, col) 順に集める。ポーズは複製。
    空のポーズのキーも「補正なし」のキーとして集める。"""
    g = doc.grid
    out: list[KeySample] = []
    for row in range(g.rows):
        for col in range(g.cols):
            p = layer.points.get((row, col))
            if p is None or not p.is_key:
                continue
            yaw, pitch = point_angles(g.yaw_range, g.pitch_range, g.cols, g.rows, row, col)
            out.append(KeySample(yaw, pitch, copy.deepcopy(p.pose)))
    return out


def add_mirror_keys(samples: list[KeySample], mirror: Mirror) -> int:
    """samples に −Yaw 側の仮想キーを足す（GenerateFromKeys の手順 2）。足した数を返す。
    占有判定は実キーと、ここまでに足した仮想キーの両方に対して行う（UE は増えていく配列を見る）。"""
    added = 0
    for key in list(samples):
        if abs(key.yaw) <= MIRROR_YAW_SKIP_DEG:
            continue  # 正面列は自分自身がミラー
        my, mp = -key.yaw, key.pitch
        if any(math.hypot(s.yaw - my, s.pitch - mp) < MIRROR_OCCUPIED_DEG for s in samples):
            continue
        samples.append(KeySample(my, mp, mirror_pose(key.pose, mirror)))
        added += 1
    return added


def _rotation_is_identity(q: Sequence[float]) -> bool:
    tol = BONE_ROTATION_IDENTITY_TOL
    return abs(q[0]) <= tol and abs(q[1]) <= tol and abs(q[2]) <= tol and abs(q[3] - 1.0) <= tol


def _interpolate(
    samples: Sequence[KeySample],
    yaw_range: float,
    pitch_range: float,
    mode: str,
    idw_power: float,
    translation_drop: float,
    yaw: float,
    pitch: float,
) -> SourcePose:
    """InterpolatePoseAtAngles の本体（samples は角度付きキーの集合）。"""
    if not samples:
        return SourcePose()

    if mode == "NearestKey":
        best: Optional[KeySample] = None
        best_dist = math.inf
        for s in samples:
            d = (s.yaw - yaw) ** 2 + (s.pitch - pitch) ** 2
            if d < best_dist:
                best_dist, best = d, s
        return copy.deepcopy(best.pose) if best is not None else SourcePose()

    # IDW
    weights: list[float] = []
    total = 0.0
    for s in samples:
        dyaw = (s.yaw - yaw) / max(yaw_range, 1.0)
        dpitch = (s.pitch - pitch) / max(pitch_range, 1.0)
        dist = math.sqrt(dyaw * dyaw + dpitch * dpitch)
        if dist < KINDA_SMALL_NUMBER:
            return copy.deepcopy(s.pose)  # 完全一致: そのキーをコピー
        w = 1.0 / (dist**idw_power)
        weights.append(w)
        total += w
    out = SourcePose()
    if not total > 0.0:
        return out

    curves: dict[str, float] = {}
    # ボーン: 平行移動の和・回転の和・最初に見つけたクォータニオン
    trans: dict[str, list[float]] = {}
    rots: dict[str, list[float]] = {}
    refs: dict[str, Quat] = {}
    for s, w in zip(samples, weights):
        alpha = w / total
        for name, v in s.pose.curves.items():
            curves[name] = curves.get(name, 0.0) + v * alpha
        for name, b in s.pose.bones.items():
            tsum = trans.setdefault(name, [0.0, 0.0, 0.0])
            for i in range(3):
                tsum[i] += b.t[i] * alpha
            q = b.r
            ref = refs.setdefault(name, q)
            if sum(q[i] * ref[i] for i in range(4)) < 0.0:  # 半球をそろえる
                q = (-q[0], -q[1], -q[2], -q[3])
            rsum = rots.setdefault(name, [0.0, 0.0, 0.0, 0.0])
            for i in range(4):
                rsum[i] += q[i] * alpha

    for name, v in curves.items():
        if abs(v) > CURVE_DROP_THRESHOLD:
            out.curves[name] = v
    for name, tsum in trans.items():
        rot = _quat_normalize(rots[name])
        if any(abs(c) > translation_drop for c in tsum) or not _rotation_is_identity(rot):
            out.bones[name] = BoneOffset(t=(tsum[0], tsum[1], tsum[2]), r=rot)
    return out


def _translation_drop(doc: Document) -> float:
    """平行移動を捨てるしきい値を文書の単位へ換算（1e-3 cm）。"""
    return BONE_TRANSLATION_DROP_CM / _space.UNIT_TO_CM[doc.meta.unit]


def _interp_for_doc(doc: Document, samples: Sequence[KeySample], yaw: float, pitch: float) -> SourcePose:
    return _interpolate(
        samples,
        doc.grid.yaw_range,
        doc.grid.pitch_range,
        doc.autogen.mode,
        doc.autogen.idw_power,
        _translation_drop(doc),
        yaw,
        pitch,
    )


def _check_mode(doc: Document) -> None:
    if doc.autogen.mode not in FILL_MODES:
        raise ValueError(f"未知の autogen.mode: {doc.autogen.mode!r}")


def interpolate_pose_at_angles(
    doc: Document, layer: Union[Layer, int], yaw: float, pitch: float, mirror: bool = False
) -> SourcePose:
    """レイヤーの実キーから任意の角度 (yaw, pitch) [度] のポーズを補間する（InterpolatePoseAtAngles。
    アルゴリズム・範囲・べき乗は doc.autogen / doc.grid）。結果は新しいポーズで、文書は変えない。
    mirror=True なら generate_from_keys と同じ仮想キーも入れる（既定は実キーだけ。リサンプル・コピーはこちら）。
    キーが 1 つも無ければ空のポーズ。"""
    _check_mode(doc)
    lay = _check_layer(doc, layer) if isinstance(layer, int) else layer
    samples = collect_keys(doc, lay)
    if mirror and doc.mirror.enabled:
        add_mirror_keys(samples, doc.mirror)
    return _interp_for_doc(doc, samples, yaw, pitch)


# --------------------------------------------------------------------------------------
# 自動生成
# --------------------------------------------------------------------------------------


def _generate_layer(doc: Document, layer: Layer, summary: AutofillSummary) -> None:
    samples = collect_keys(doc, layer)
    if not samples:
        summary.skipped_layers.append(layer.name)
        return  # UE: キーが無ければ何もしない（既存の自動生成の点も残る）
    n_keys = len(samples)
    n_mirror = add_mirror_keys(samples, doc.mirror) if doc.mirror.enabled else 0

    g = doc.grid
    for row in range(g.rows):
        for col in range(g.cols):
            existing = layer.points.get((row, col))
            if existing is not None and existing.is_key:
                continue  # キーは触らない
            yaw, pitch = point_angles(g.yaw_range, g.pitch_range, g.cols, g.rows, row, col)
            pose = _interp_for_doc(doc, samples, yaw, pitch)
            if pose.is_empty():
                if existing is not None:
                    del layer.points[(row, col)]
                    summary.cleared += 1
                continue
            if existing is not None:
                existing.pose = pose
            else:
                layer.points[(row, col)] = GridPoint(row, col, False, pose)
            summary.generated += 1
    summary.layers += 1
    summary.keys += n_keys
    summary.mirror_keys += n_mirror


def generate_from_keys(doc: Document, layer_index: Optional[int] = None) -> AutofillSummary:
    """キーから残りの点を埋める（GenerateFromKeys）。layer_index=None は全レイヤー、整数ならそのレイヤーだけ。
    キーは変えない。非キーの点（以前の自動生成を含む）は書き換え、結果が空なら消す。同じ入力で 2 回呼んでも結果は同じ。
    ベイクの状態は Maya シーン側が持つので、呼ぶ側が「生成した点は未ベイク」として扱う。"""
    _check_mode(doc)
    summary = AutofillSummary()
    if layer_index is None:
        targets = list(doc.layers)
    else:
        targets = [_check_layer(doc, layer_index)]
    for layer in targets:
        _generate_layer(doc, layer, summary)
    return summary


# --------------------------------------------------------------------------------------
# 格子サイズの変更
# --------------------------------------------------------------------------------------


def resize_grid(
    doc: Document,
    cols: int,
    rows: int,
    yaw_range: Optional[float] = None,
    pitch_range: Optional[float] = None,
) -> ResizeSummary:
    """格子の分割数（と範囲）を変える。作った点は**角度で**引き継ぐ（docs/14 §5.2）。

    規則（レイヤーごと）:
    1. 旧格子の実キーの角度（旧の yaw / pitch 範囲で計算）を覚える。非キーの点は引き継がず、実キーからの再サンプルで作り直す
    2. 新しい格子の各点について、1e-3° 以内に旧キーがあればそのキーのポーズをキーのまま置く
    3. 重ならない点は旧キーから `interpolate_pose_at_angles` で補間し（ミラーの仮想キーは使わない。
       距離の正規化・べき乗・アルゴリズムは新しい格子の範囲と doc.autogen）、結果が空でなければ非キー点として置く。
       キーが無いレイヤーは何も置かない
    4. どの新しい点にも重ならなかった旧キーは捨て、`dropped_keys` に入れる（UI が警告を出す）
    旧格子の外にあった点は捨てる。ミラーの補完をしたいときは、呼んだあとに generate_from_keys を呼ぶ。
    UE 版の EnsureGridSize は (row, col) の番号が重なる点をそのまま残し、角度は見ない（範囲が変わるとキーの角度が動く）。
    """
    if cols < 1 or rows < 1:
        raise ValueError("cols / rows は 1 以上")
    _check_mode(doc)
    g = doc.grid
    old_cols, old_rows, old_yaw, old_pitch = g.cols, g.rows, g.yaw_range, g.pitch_range
    new_yaw = old_yaw if yaw_range is None else yaw_range
    new_pitch = old_pitch if pitch_range is None else pitch_range

    old_keys: dict[int, list[tuple[int, int, float, float, GridPoint]]] = {}
    for li, layer in enumerate(doc.layers):
        keys = []
        for row in range(old_rows):
            for col in range(old_cols):
                p = layer.points.get((row, col))
                if p is not None and p.is_key:
                    y, pt = point_angles(old_yaw, old_pitch, old_cols, old_rows, row, col)
                    keys.append((row, col, y, pt, p))
        old_keys[li] = keys

    # 新しい格子の設定で補間するため先に格子を更新し、失敗したら戻す
    g.cols, g.rows, g.yaw_range, g.pitch_range = cols, rows, new_yaw, new_pitch
    summary = ResizeSummary()
    try:
        new_points: dict[int, dict[tuple[int, int], GridPoint]] = {}
        for li, layer in enumerate(doc.layers):
            keys = old_keys[li]
            samples = [KeySample(y, pt, copy.deepcopy(p.pose)) for (_, _, y, pt, p) in keys]
            used = [False] * len(keys)
            pts: dict[tuple[int, int], GridPoint] = {}
            for row in range(rows):
                for col in range(cols):
                    yaw, pitch = point_angles(new_yaw, new_pitch, cols, rows, row, col)
                    hit = None
                    best = RESIZE_KEY_MATCH_DEG
                    for i, (_, _, ky, kp, _) in enumerate(keys):
                        d = math.hypot(ky - yaw, kp - pitch)
                        if d <= best:
                            hit, best = i, d
                    if hit is not None:
                        old = keys[hit][4]
                        used[hit] = True
                        pts[(row, col)] = GridPoint(
                            row, col, True, copy.deepcopy(old.pose), copy.deepcopy(old.extra)
                        )
                        summary.kept_keys += 1
                        continue
                    pose = _interp_for_doc(doc, samples, yaw, pitch)
                    if not pose.is_empty():
                        pts[(row, col)] = GridPoint(row, col, False, pose)
                        summary.resampled += 1
            for i, (r, c, y, pt, _) in enumerate(keys):
                if not used[i]:
                    summary.dropped_keys.append(DroppedKey(layer.name, r, c, y, pt))
            new_points[li] = pts
    except Exception:
        g.cols, g.rows, g.yaw_range, g.pitch_range = old_cols, old_rows, old_yaw, old_pitch
        raise
    for li, layer in enumerate(doc.layers):
        layer.points = new_points[li]
    return summary


# --------------------------------------------------------------------------------------
# データ間のコピー
# --------------------------------------------------------------------------------------

COPY_FLAGS = ("all_layers", "working_set_only", "keys_only")


def parse_copy_mode(mode: Union[str, Iterable[str]]) -> set[str]:
    """モードの指定を旗の集合にする。"all_layers" / "working_set_only" / "keys_only" のどれか、または
    "all_layers+keys_only" のように "+" でつなぐ、あるいは文字列の列。旗は UE の FFacialCopyOptions の 3 つの bool と同じで独立。
    指定した旗だけが true（UE の既定は keys_only=true だが、ここでは明示する）。"""
    parts: list[str] = []
    if isinstance(mode, str):
        parts = [p.strip() for p in mode.split("+") if p.strip()]
    else:
        parts = list(mode)
    flags = set(parts)
    unknown = flags - set(COPY_FLAGS)
    if unknown:
        raise ValueError(f"未知のコピーモード: {sorted(unknown)}（{COPY_FLAGS}）")
    return flags


def _copy_samples(
    src: Document,
    dst: Document,
    src_layer: Layer,
    flags: set[str],
    cv: Optional[_space.Converter],
) -> list[KeySample]:
    g = src.grid
    filt_curves = "working_set_only" in flags and bool(dst.working_set.curves)
    filt_bones = "working_set_only" in flags and bool(dst.working_set.bones)
    samples: list[KeySample] = []
    for row in range(g.rows):
        for col in range(g.cols):
            p = src_layer.points.get((row, col))
            if p is None:
                continue
            if "keys_only" in flags and not p.is_key:
                continue
            pose = SourcePose()
            for name, v in p.pose.curves.items():
                if filt_curves and name not in dst.working_set.curves:
                    continue
                pose.curves[name] = v
            for name, b in p.pose.bones.items():
                if filt_bones and name not in dst.working_set.bones:
                    continue
                pose.bones[name] = cv.bone_offset(b) if cv is not None else copy.deepcopy(b)
            yaw, pitch = point_angles(g.yaw_range, g.pitch_range, g.cols, g.rows, row, col)
            samples.append(KeySample(yaw, pitch, pose))
    return samples


def copy_from(
    src_doc: Document,
    dst_doc: Document,
    mode: Union[str, Iterable[str]] = "keys_only",
    src_layer_index: int = 0,
    dst_layer_index: int = 0,
) -> CopyReport:
    """別の文書（格子の大きさ・範囲は違ってよい）からポーズをコピーする（FFacialCorrectionCopier::CopyFromAsset）。

    - mode: `parse_copy_mode` を参照。"all_layers" は名前が同じレイヤー同士（宛先に無ければ新規作成、上限 MAX_LAYERS）、
      そうでなければ src_layer_index → dst_layer_index の 1 組（UE の「アクティブレイヤー」に当たる）
    - "keys_only" ならソースのキーだけ、そうでなければソースの作った点（自動生成を含む）を、角度付きのサンプルにする
    - "working_set_only" なら、宛先の作業セットが空でない側（カーブ / ボーン）だけ、名前が作業セットにあるものに絞る
    - サンプルを宛先の格子の角度で `_interpolate`（宛先の範囲・アルゴリズム・べき乗。ミラーの仮想キーは使わない）で再サンプルし、
      結果が空でない点を**非キー**として書き込む（宛先にあったキーも上書きしてキーでなくなる。UE 準拠）。空の結果の点は触らない
    - ソースにサンプルが無いレイヤーは何もしない。名前の照合は完全一致で、宛先のシーンに無い名前の検査はここではしない（検証に任せる）
    - 座標系（meta）が違えばボーンのずらしを宛先の系へ変換する。src_doc と dst_doc が同じオブジェクトなら何もしない
    """
    report = CopyReport()
    if src_doc is dst_doc:
        return report
    _check_mode(dst_doc)
    flags = parse_copy_mode(mode)
    s_spec, d_spec = _space.space_of(src_doc.meta), _space.space_of(dst_doc.meta)
    cv = None if s_spec == d_spec else _space.converter(s_spec, d_spec)

    pairs: list[tuple[Layer, Layer]] = []
    if "all_layers" in flags:
        for sl in src_doc.layers:
            dl = next((l for l in dst_doc.layers if l.name == sl.name), None)
            if dl is None:
                if len(dst_doc.layers) >= MAX_LAYERS:
                    report.layer_limit_reached.append(sl.name)
                    continue
                dl = Layer(name=sl.name)
                dst_doc.layers.append(dl)
                report.created_layers.append(sl.name)
            pairs.append((sl, dl))
    else:
        pairs.append((_check_layer(src_doc, src_layer_index), _check_layer(dst_doc, dst_layer_index)))

    g = dst_doc.grid
    for sl, dl in pairs:
        samples = _copy_samples(src_doc, dst_doc, sl, flags, cv)
        if not samples:
            report.skipped_layers.append(sl.name)
            continue
        for row in range(g.rows):
            for col in range(g.cols):
                yaw, pitch = point_angles(g.yaw_range, g.pitch_range, g.cols, g.rows, row, col)
                pose = _interp_for_doc(dst_doc, samples, yaw, pitch)
                if pose.is_empty():
                    continue
                existing = dl.points.get((row, col))
                if existing is not None:
                    existing.pose = pose
                    existing.is_key = False
                else:
                    dl.points[(row, col)] = GridPoint(row, col, False, pose)
                report.copied_points += 1
        report.copied_layers += 1
    return report
