"""検証・似た名前の候補・一括改名（Maya 非依存）。docs/14 §5.9（R-31）、UE 版 `FacialCorrectionValidator` / `FacialReferenceRepair` の写し + 拡張。

- `validate(doc, scene_info, profile=None, bake_state=None)` は Document と「シーンの事情」（SceneInfo。呼ぶ側が Maya から集める）だけで
  検出できるものを Issue の一覧にして返す。Maya には触らない。SceneInfo の各欄が None = 「その情報は渡されていない」→ その検査は飛ばす
  （空の集合 = 「無い」と確かめた、とは区別する）。bake_state が None のときも未ベイク系の検査は飛ばす
- 名前の照合は大文字小文字を区別する完全一致（UE の FName は区別しないので、UE 版にない「大小違いだけの不一致」を別の項目にした）
- 欠けた名前は黙って 0 として扱われる（止めない）ので、参照切れ・大小違いは warning（致命的なのは error）
- `suggest_names` は UE 版 `SuggestSimilarNames`（小文字化した編集距離。打ち切りの閾値も同じ）。同点は difflib の類似度、それも同点なら元の並び
- `rename_references` は UE 版 `ApplyCurveRenames` / `ApplyBoneRenames` と同じ 2 パス方式（同じポーズ内で改名先が衝突するときは、
  値を失わないようその点では改名を見送る）
- `pose_hash` は「ベイク時のポーズ」の指紋。Maya シーン側（docs/15 §4.1）が morph 名 → ハッシュで持ち、ここで比べる
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Collection, Iterable, Mapping, Optional, Sequence

from . import naming
from . import space
from .model import FILL_MODES, FORWARD_AXES, MAX_LAYERS, MIRROR_AXES, Document, SourcePose
from .profile import (
    DEFAULT_LIMIT,
    NamingProfile,
    effective_limit,
    has_limit,
    is_mirror_excluded,
    mirror_name,
    missing_standard_curves,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

NEUTRAL_LAYER = "Neutral"
KIND_CURVE = "curve"
KIND_BONE = "bone"

_LIMIT_TOL = 1e-9
_LAYER_NAME_OK = re.compile(r"^[A-Za-z0-9_]+$")  # Maya のノード名・ブレンドシェイプ名に安全な字


# ---------------------------------------------------------------------------
# データ
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """検出 1 件。layer は Document.layers の index、row / col は点の位置（無ければ None）。

    suggestion = 直し方の第一候補（改名先など）、candidates = 候補の全部（近い順）。
    """

    code: str
    severity: str
    message: str
    layer: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    name: str = ""
    suggestion: str = ""
    candidates: tuple[str, ...] = ()


@dataclass
class TargetInfo:
    """ブレンドシェイプのターゲット 1 本の情報（任意）。"""

    vertex_count: Optional[int] = None  # 差分を持つ頂点の数
    empty: bool = False  # 差分が 1 つも無い


@dataclass
class SceneInfo:
    """検証に要る「シーン（モデル）の事情」。呼ぶ側が集めて渡す。None = 渡されていない（その検査は飛ばす）。"""

    curves: Optional[Collection[str]] = None  # モデルにあるシェイプ名（`bs.eye_close_L` の形）
    bones: Optional[Collection[str]] = None  # モデルにあるボーン名
    bone_parents: Optional[Mapping[str, str]] = None  # ボーン名 → 親の名前（根は ""）
    targets: Optional[Collection[str]] = None  # 顔メッシュにある FC_* / fcs_* のターゲット名
    recorded_bone_parents: Optional[Mapping[str, str]] = None  # 前に記録した親（UE 版の RecordedBoneParents に当たる）
    target_info: Mapping[str, TargetInfo] = field(default_factory=dict)  # ターゲット名 → 情報


# ---------------------------------------------------------------------------
# ポーズの指紋
# ---------------------------------------------------------------------------

_HASH_SCALE = 1_000_000  # 1e-6 に丸める


def _q(v: float) -> int:
    """1e-6 に丸めた整数（-0 を作らない）。"""
    return int(round(v * _HASH_SCALE))


def normalize_exclude(patterns) -> list[str]:
    """除外パターンの正規化（前後の空白を除き、空を捨て、重複を除いて並べ替える）。"""
    return sorted({str(x).strip() for x in patterns if str(x).strip()})


def exclude_signature(doc: Document) -> str:
    """補正から除外するもの（シェイプ・ボーン）の指紋（正規化した値の sha1 の先頭 12 文字）。ベイク時に記録して今と比べる。"""
    norm = json.dumps(
        {"curves": normalize_exclude(doc.exclude.curves), "bones": normalize_exclude(doc.exclude.bones)}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def pose_hash(pose: SourcePose) -> str:
    """SourcePose の安定したハッシュ（sha1 の 16 進）。

    - 値は 1e-6 に丸める。並びに依存しない（名前の辞書順で正規化）
    - 丸めて 0 になるシェイプの重み・恒等のボーン（t = 0、r = 恒等、s = 1）は「無い」のと同じ扱い（ベイクの結果が同じため）
    """
    curves = sorted((k, _q(v)) for k, v in pose.curves.items() if _q(v) != 0)
    bones = []
    for name, b in sorted(pose.bones.items()):
        t = tuple(_q(x) for x in b.t)
        r = tuple(_q(x) for x in b.r)
        s = tuple(_q(x) for x in b.s)
        if t == (0, 0, 0) and r == (0, 0, 0, _HASH_SCALE) and s == (_HASH_SCALE,) * 3:
            continue
        bones.append((name, t, r, s))
    payload = repr((curves, bones)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


# ---------------------------------------------------------------------------
# 似た名前の候補（UE 版 FFacialReferenceRepair::SuggestSimilarNames）
# ---------------------------------------------------------------------------


def levenshtein(a: str, b: str) -> int:
    """編集距離（挿入・削除・置換が各 1）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def suggest_names(missing: str, candidates: Iterable[str], limit: int = 3) -> list[str]:
    """無い名前 `missing` に近い候補（近い順、最大 limit 件）。

    UE 版と同じ: 小文字にした編集距離で並べ、距離が `max(len, 4) // 2 + 2` を超える候補は無関係として捨てる。
    UE 版の並べ替えは不安定（同点の順が決まらない）なので、同点は difflib の類似度（高いほう）→ 元の並びで決める。
    大小違いだけの名前は距離 0 なので先頭に来る。
    """
    low = missing.lower()
    max_allowed = max(len(low), 4) // 2 + 2
    scored = []
    for i, cand in enumerate(candidates):
        c = cand.lower()
        d = levenshtein(low, c)
        if d > max_allowed:
            continue
        scored.append((d, -difflib.SequenceMatcher(None, low, c).ratio(), i, cand))
    scored.sort()
    return [s[3] for s in scored[: max(limit, 0)]]


def case_only_match(name: str, candidates: Iterable[str]) -> str:
    """大小違いだけで一致する本当の綴り（無ければ ""）。複数あるときは並びの先頭。"""
    low = name.lower()
    for c in candidates:
        if c != name and c.lower() == low:
            return c
    return ""


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    return f"{v:g}"


def _iter_points(doc: Document):
    """(layer index, layer, (row, col), point)。位置の順。"""
    for li, layer in enumerate(doc.layers):
        for rc in sorted(layer.points):
            yield li, layer, rc, layer.points[rc]


def _in_grid(doc: Document, rc: tuple[int, int]) -> bool:
    return 0 <= rc[0] < doc.grid.rows and 0 <= rc[1] < doc.grid.cols


EXTREME_EPS = 1e-6  # 重みが 1 を超えているとみなす余裕（pose_hash の丸めと同じ 1e-6）


def extreme_curves(doc: Document, pose: SourcePose) -> list[str]:
    """ポーズのうち、重みが 1 を超えるシェイプの名前（誇張。R-37）。補正除外に当たるものは焼かれないので数えない。"""
    return [n for n, w in pose.curves.items() if w > 1.0 + EXTREME_EPS and not is_mirror_excluded(n, doc.exclude.curves)]


def has_extreme(doc: Document, pose: SourcePose) -> bool:
    """ポーズに重み 1 超のシェイプがあるか（あれば `_Ex` を焼く）。"""
    return bool(extreme_curves(doc, pose))


def clamp_extreme(pose: SourcePose) -> SourcePose:
    """重みを 1 までに丸めたポーズの複製（誇張を除いた「通常の」形を焼くため）。ボーンはそのまま。"""
    out = SourcePose()
    out.curves = {n: min(w, 1.0) for n, w in pose.curves.items()}
    out.bones = dict(pose.bones)
    return out


def needs_extreme(doc: Document, layer_index: int, rc: tuple[int, int]) -> bool:
    """この点に `_Ex` を焼くか。
    - その点のポーズに重み 1 超のシェイプがある
    - または感情レイヤーで、差分ベイク（bake.differential）が有効、かつ Neutral の同じ位置の点に重み 1 超がある
      （感情の差分は Neutral の通常 / 誇張それぞれを引いて焼くため、Neutral に誇張があると感情側にも誇張の差分が要る）
    """
    layer = doc.layers[layer_index]
    pt = layer.points.get(rc)
    if pt is None or pt.pose.is_empty() or not _in_grid(doc, rc):
        return False
    if has_extreme(doc, pt.pose):
        return True
    differential = doc.bake.differential if doc.bake is not None else True
    if layer_index > 0 and differential:
        npt = doc.layers[0].points.get(rc)
        return npt is not None and has_extreme(doc, npt.pose)
    return False


def _where(li: Optional[int], doc: Document, rc: Optional[tuple[int, int]]) -> str:
    if li is None:
        return ""
    name = doc.layers[li].name if li < len(doc.layers) else str(li)
    return f"{name} (行 {rc[0]}, 列 {rc[1]})" if rc else name


def validate(
    doc: Document,
    scene_info: SceneInfo,
    profile: Optional[NamingProfile] = None,
    bake_state: Optional[Mapping[str, str]] = None,
    bake_exclude: Optional[Mapping[str, str]] = None,
) -> list[Issue]:
    """Document を検証して Issue の一覧を返す（コードの一覧は docs/14 §5.9）。Document もシーンも変更しない。

    bake_state = ベイク済みのターゲット名（FC_*） → ベイク時の pose_hash。None なら未ベイク系の検査は飛ばす。
    bake_exclude = ターゲット名 → ベイク時の除外パターンの指紋（`exclude_signature`）。今と違えば「ベイク後に変更」。
    None・記録の無い名前は「不明 = 今と同じ」とみなす（従来のデータが一斉に変更ありにならない）。
    """
    issues: list[Issue] = []
    add = issues.append
    scene = scene_info
    asset = doc.asset or ""

    # --- 構造（格子・レイヤー）---
    if not asset:
        add(Issue("asset_missing", SEVERITY_ERROR, "アセット名（asset）が空です。FC_<asset>_… の名前を作れません"))
    _check_values(doc, add)
    _check_grid(doc, scene, add)
    _check_layers(doc, add)
    _check_layer_weights(doc, add)

    # --- 参照（シェイプ・ボーン）---
    curve_refs, bone_refs = _collect_refs(doc)
    if scene.curves is not None:
        _check_missing(KIND_CURVE, curve_refs, set(scene.curves), doc, add)
    if scene.bones is not None:
        _check_missing(KIND_BONE, bone_refs, set(scene.bones), doc, add)
    _check_mirror(doc, scene, profile, curve_refs, bone_refs, add)
    _check_bone_parents(doc, scene, bone_refs, add)

    # --- 可動域 ---
    _check_limits(doc, profile, add)

    # --- 補正除外（R-17）---
    _check_excluded(doc, add)

    # --- プロファイル ---
    if profile is not None and scene.curves is not None:
        for n in missing_standard_curves(profile, scene.curves):
            hint = suggest_names(n, scene.curves)
            add(
                Issue(
                    "profile_standard_missing",
                    SEVERITY_INFO,
                    f"プロファイル「{profile.name}」の標準シェイプ「{n}」がモデルにありません",
                    name=n,
                    suggestion=hint[0] if hint else "",
                    candidates=tuple(hint),
                )
            )

    # --- ベイク・ターゲット ---
    if asset:
        _check_bake(doc, scene, bake_state, add, bake_exclude)
        _check_targets(doc, scene, add)
    return issues


MAX_GRID_SIDE = 64  # 格子の列数・行数の上限（画面の入力欄と同じ。これを超えると自動生成・ベイクが終わらない）
MAX_IDW_POWER = 64.0  # IDW の指数の上限（これを超えると 1 / dist**p が 0 除算になる）


def _walk_numbers(v, path: str):
    """辞書・リストの中の数値（float）を (場所, 値) で列挙する（再帰を使わない）。"""
    stack = [(v, path)]
    while stack:
        cur, where = stack.pop()
        if isinstance(cur, dict):
            stack.extend((c, f"{where}.{k}" if where else str(k)) for k, c in cur.items())
        elif isinstance(cur, (list, tuple)):
            stack.extend((c, f"{where}[{i}]") for i, c in enumerate(cur))
        elif isinstance(cur, float):
            yield where, cur


def _check_values(doc: Document, add) -> None:
    """読み込みでは通るが、あとで落ちる・結果が壊れる値（有限でない数・ミラー軸・自動生成の設定・単位・格子の大きさ。docs/19 C-4 / C-5）。"""
    from . import fcpose_io  # 書き出しと同じ辞書で調べる（遅延 import: fcpose_io は validate に依存しない）

    try:
        d = fcpose_io.to_dict(doc)
    except Exception:  # noqa: BLE001  辞書にできない = 下の検査で個別に見る
        d = {}
    bad = [where for where, val in _walk_numbers(d, "") if not math.isfinite(val)]
    for where in bad[:20]:
        add(Issue("non_finite_value", SEVERITY_ERROR, f"有限でない数値（NaN / 無限大）があります: {where}。保存・ベイクできません", name=where))
    if len(bad) > 20:
        add(Issue("non_finite_value", SEVERITY_ERROR, f"有限でない数値がほかに {len(bad) - 20} 個あります"))
    if doc.mirror.bone_axis not in MIRROR_AXES:
        add(
            Issue(
                "mirror_axis_invalid",
                SEVERITY_ERROR,
                f"ミラーの軸「{doc.mirror.bone_axis}」は使えません（{' / '.join(MIRROR_AXES)}。大文字で指定）",
                name=doc.mirror.bone_axis,
            )
        )
    if doc.autogen.mode not in FILL_MODES:
        add(Issue("autogen_invalid", SEVERITY_ERROR, f"自動生成の方式「{doc.autogen.mode}」は使えません（{' / '.join(FILL_MODES)}）", name=doc.autogen.mode))
    p = doc.autogen.idw_power
    if not (math.isfinite(p) and 0.0 < p <= MAX_IDW_POWER):
        add(Issue("autogen_invalid", SEVERITY_ERROR, f"自動生成の IDW の指数 {_fmt(p)} が範囲外です（0 より大きく {MAX_IDW_POWER:g} 以下）", name="idwPower"))
    m = doc.meta
    if m.unit not in space.UNIT_TO_CM or m.up_axis not in ("Y", "Z") or m.handedness not in ("left", "right"):
        add(
            Issue(
                "meta_invalid",
                SEVERITY_ERROR,
                f"座標系の記録（単位 {m.unit} / 上軸 {m.up_axis} / {m.handedness}）が使えない値です",
            )
        )
    if doc.grid.cols > MAX_GRID_SIDE or doc.grid.rows > MAX_GRID_SIDE:
        add(
            Issue(
                "grid_size_invalid",
                SEVERITY_ERROR,
                f"格子の大きさ（{doc.grid.cols} 列 × {doc.grid.rows} 行）が大きすぎます。列・行とも {MAX_GRID_SIDE} 以下にしてください",
            )
        )


def _check_grid(doc: Document, scene: SceneInfo, add) -> None:
    g = doc.grid
    if g.cols < 2 or g.rows < 2:
        add(
            Issue(
                "grid_size_invalid",
                SEVERITY_ERROR,
                f"格子の大きさ（{g.cols} 列 × {g.rows} 行）が小さすぎます。列・行とも 2 以上が必要です",
            )
        )
    if g.yaw_range <= 0 or g.pitch_range <= 0:
        add(
            Issue(
                "grid_range_invalid",
                SEVERITY_WARNING,
                f"格子の角度の範囲（Yaw {_fmt(g.yaw_range)}°、Pitch {_fmt(g.pitch_range)}°）が 0 以下です",
            )
        )
    if g.forward_axis not in FORWARD_AXES:
        add(
            Issue(
                "forward_axis_invalid",
                SEVERITY_ERROR,
                f"forwardAxis「{g.forward_axis}」は使えません（{' / '.join(FORWARD_AXES)}）",
                name=g.forward_axis,
            )
        )
    elif g.forward_axis[1:] == doc.meta.up_axis:
        add(
            Issue(
                "forward_axis_invalid",
                SEVERITY_ERROR,
                f"forwardAxis「{g.forward_axis}」は上軸（{doc.meta.up_axis}）です。上向きは前方向にできません",
                name=g.forward_axis,
            )
        )
    # 基準ボーン
    if scene.bones is not None:
        bones = set(scene.bones)
        if not g.base_bone:
            add(Issue("base_bone_missing", SEVERITY_ERROR, "基準ボーン（baseBone）が設定されていません"))
        elif g.base_bone not in bones:
            real = case_only_match(g.base_bone, sorted(bones))
            if real:
                add(
                    Issue(
                        "bone_case_mismatch",
                        SEVERITY_WARNING,
                        f"基準ボーン「{g.base_bone}」は大文字小文字が違います（モデルでは「{real}」）",
                        name=g.base_bone,
                        suggestion=real,
                        candidates=(real,),
                    )
                )
            else:
                hint = suggest_names(g.base_bone, sorted(bones))
                add(
                    Issue(
                        "base_bone_missing",
                        SEVERITY_ERROR,
                        f"基準ボーン「{g.base_bone}」がモデルにありません",
                        name=g.base_bone,
                        suggestion=hint[0] if hint else "",
                        candidates=tuple(hint),
                    )
                )


def _check_layers(doc: Document, add) -> None:
    layers = doc.layers
    if not layers:
        add(Issue("layer0_not_neutral", SEVERITY_ERROR, f"レイヤーがありません。レイヤー 0 は「{NEUTRAL_LAYER}」が必要です"))
    elif layers[0].name != NEUTRAL_LAYER:
        add(
            Issue(
                "layer0_not_neutral",
                SEVERITY_ERROR,
                f"レイヤー 0 の名前が「{layers[0].name}」です。「{NEUTRAL_LAYER}」にしてください",
                layer=0,
                name=layers[0].name,
                suggestion=NEUTRAL_LAYER,
            )
        )
    if len(layers) > MAX_LAYERS:
        add(
            Issue(
                "layer_count_exceeded",
                SEVERITY_ERROR,
                f"レイヤーが {len(layers)} 個あります（Neutral を含めて最大 {MAX_LAYERS}）",
            )
        )

    seen: dict[str, int] = {}
    seen_lower: dict[str, int] = {}
    reported_dup: set[str] = set()
    reported_case: set[str] = set()
    for li, layer in enumerate(layers):
        n = layer.name
        if not n:
            add(Issue("layer_name_empty", SEVERITY_ERROR, "名前が空のレイヤーがあります", layer=li))
        elif not _LAYER_NAME_OK.match(n):
            add(
                Issue(
                    "layer_name_chars",
                    SEVERITY_WARNING,
                    f"レイヤー名「{n}」に英数字と _ 以外が含まれます（ターゲット名・FBX で問題になることがあります）",
                    layer=li,
                    name=n,
                )
            )
        if n == naming.PERSPECTIVE_LAYER:
            add(
                Issue(
                    "layer_name_reserved",
                    SEVERITY_ERROR,
                    f"レイヤー名「{n}」はパース補正の名前（FC_<asset>_{n}_K{{n}}）と衝突します",
                    layer=li,
                    name=n,
                )
            )
        if n in seen:
            if n not in reported_dup:
                reported_dup.add(n)
                add(
                    Issue(
                        "layer_name_duplicate",
                        SEVERITY_ERROR,
                        f"レイヤー名が重複しています: {n}（ベイクで作るシェイプの名前が衝突し、上書きし合います）",
                        layer=li,
                        name=n,
                    )
                )
        else:
            seen[n] = li
            low = n.lower()
            if low in seen_lower and seen_lower[low] != li and low not in reported_case:
                reported_case.add(low)
                add(
                    Issue(
                        "layer_name_case_collision",
                        SEVERITY_WARNING,
                        f"レイヤー名「{n}」と「{layers[seen_lower[low]].name}」は大文字小文字だけの違いです"
                        "（UE など大小を区別しない環境ではシェイプ名が衝突します）",
                        layer=li,
                        name=n,
                    )
                )
            seen_lower.setdefault(low, li)

        if not any(not p.pose.is_empty() for p in layer.points.values()):
            add(Issue("layer_empty", SEVERITY_INFO, f"空のレイヤー: {n}", layer=li, name=n))

    # 点が格子の外
    for li, layer, rc, pt in _iter_points(doc):
        if not _in_grid(doc, rc):
            add(
                Issue(
                    "point_outside_grid",
                    SEVERITY_WARNING,
                    f"点 {_where(li, doc, rc)} が格子（{doc.grid.rows} 行 × {doc.grid.cols} 列）の外です。ベイクされません",
                    layer=li,
                    row=rc[0],
                    col=rc[1],
                )
            )

    prefix = doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX
    if not prefix or prefix.startswith(naming.FC_PREFIX):
        add(
            Issue(
                "sculpt_prefix_invalid",
                SEVERITY_ERROR,
                f"彫り用シェイプの接頭辞「{prefix}」は使えません（空、または FC_ で始まるとベイクの掃除に巻き込まれます）",
                name=prefix,
            )
        )


# --- 参照 ---

_Loc = Optional[tuple[int, tuple[int, int]]]  # (layer index, (row, col))。作業セットなど点以外は None


LAYER_WEIGHT_SOURCES = ("direct", "curve", "distance")


def _check_layer_weights(doc: Document, add) -> None:
    """重みの出どころ（layerWeights。R-35）: 存在しないレイヤー・知らない種類・距離の指定の不備。"""
    if not doc.layer_weights:
        return
    names = {l.name for l in doc.layers}
    for name, spec in doc.layer_weights.items():
        if name not in names:
            add(Issue("layer_weight_unknown_layer", SEVERITY_WARNING, f"重みの出どころの設定が、存在しないレイヤー「{name}」を指しています", name=name))
            continue
        src = spec.get("source", "curve")
        if src not in LAYER_WEIGHT_SOURCES:
            add(Issue("layer_weight_invalid", SEVERITY_WARNING, f"レイヤー「{name}」の重みの出どころ「{src}」は知らない種類です（直接 / カーブ / 距離）", name=name))
            continue
        if src != "distance":
            continue
        vals = [spec.get(k) for k in ("start", "end", "from", "to")]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in vals):
            add(Issue("layer_weight_invalid", SEVERITY_WARNING, f"レイヤー「{name}」の距離の設定（開始・終了・から・まで）に数値でないものがあります", name=name))
        elif vals[0] < 0 or vals[1] < 0 or not (0.0 <= vals[2] <= 1.0 and 0.0 <= vals[3] <= 1.0):
            add(Issue("layer_weight_invalid", SEVERITY_WARNING, f"レイヤー「{name}」の距離の設定が範囲外です（距離は 0 以上、重みは 0〜1）", name=name))


def _collect_refs(doc: Document) -> tuple[dict[str, list[_Loc]], dict[str, list[_Loc]]]:
    """使われているシェイプ名・ボーン名 → 使われている場所。（基準ボーンは別に検査する）"""
    curves: dict[str, list[_Loc]] = {}
    bones: dict[str, list[_Loc]] = {}
    for li, layer, rc, pt in _iter_points(doc):
        for n in pt.pose.curves:
            curves.setdefault(n, []).append((li, rc))
        for n in pt.pose.bones:
            bones.setdefault(n, []).append((li, rc))
    for n in doc.working_set.curves:
        curves.setdefault(n, []).append(None)
    for n in doc.intensity_curves:
        curves.setdefault(n, []).append(None)
    for n in doc.working_set.bones:
        bones.setdefault(n, []).append(None)
    return curves, bones


def _check_missing(kind: str, refs: dict[str, list[_Loc]], available: set[str], doc: Document, add) -> None:
    label = "シェイプ" if kind == KIND_CURVE else "ボーン"
    pool = sorted(available)
    for name, locs in refs.items():
        if name in available:
            continue
        first = next((loc for loc in locs if loc is not None), None)
        li, rc = first if first else (None, None)
        count = len(locs)
        used = f"（{count} か所で使用）" if count > 1 else ""
        real = case_only_match(name, pool)
        if real:
            add(
                Issue(
                    f"{kind}_case_mismatch",
                    SEVERITY_WARNING,
                    f"{label}「{name}」は大文字小文字が違います（モデルでは「{real}」）{used}",
                    layer=li,
                    row=rc[0] if rc else None,
                    col=rc[1] if rc else None,
                    name=name,
                    suggestion=real,
                    candidates=(real,),
                )
            )
            continue
        hint = suggest_names(name, pool)
        add(
            Issue(
                f"{kind}_missing",
                SEVERITY_WARNING,
                f"{label}「{name}」がモデルにありません{used}。0 として扱われます",
                layer=li,
                row=rc[0] if rc else None,
                col=rc[1] if rc else None,
                name=name,
                suggestion=hint[0] if hint else "",
                candidates=tuple(hint),
            )
        )


def _check_mirror(
    doc: Document,
    scene: SceneInfo,
    profile: Optional[NamingProfile],
    curve_refs: dict[str, list[_Loc]],
    bone_refs: dict[str, list[_Loc]],
    add,
) -> None:
    m = doc.mirror
    if not m.enabled:
        return
    if not m.suffix_l or not m.suffix_r or m.suffix_l == m.suffix_r:
        add(
            Issue(
                "mirror_suffix_invalid",
                SEVERITY_WARNING,
                f"ミラーの接尾辞（L =「{m.suffix_l}」、R =「{m.suffix_r}」）が空、または同じです。鏡映できません",
            )
        )
        return
    patterns = list(m.exclude) + (list(profile.mirror.exclude) if profile is not None else [])
    for kind, refs, avail in (
        (KIND_CURVE, curve_refs, scene.curves),
        (KIND_BONE, bone_refs, scene.bones),
    ):
        if avail is None:
            continue
        have = set(avail)
        label = "シェイプ" if kind == KIND_CURVE else "ボーン"
        for name in refs:
            if name not in have or is_mirror_excluded(name, patterns):
                continue
            partner = mirror_name(name, m.suffix_l, m.suffix_r)
            if partner != name and partner not in have:
                first = next((loc for loc in refs[name] if loc is not None), None)
                li, rc = first if first else (None, None)
                add(
                    Issue(
                        "mirror_partner_missing",
                        SEVERITY_WARNING,
                        f"{label}「{name}」の左右の相手「{partner}」がモデルにありません（ミラーできません。"
                        "鏡映しない名前なら mirror.exclude に入れてください）",
                        layer=li,
                        row=rc[0] if rc else None,
                        col=rc[1] if rc else None,
                        name=name,
                        suggestion=partner,
                    )
                )


def _check_bone_parents(doc: Document, scene: SceneInfo, bone_refs: dict[str, list[_Loc]], add) -> None:
    """UE 版: ボーン階層（親）が記録時から変わっている（親ボーン空間の加算の意味が変わる）。"""
    if scene.bone_parents is None or scene.recorded_bone_parents is None:
        return
    names = list(bone_refs)
    if doc.grid.base_bone and doc.grid.base_bone not in bone_refs:
        names.append(doc.grid.base_bone)
    for n in names:
        old = scene.recorded_bone_parents.get(n)
        cur = scene.bone_parents.get(n)
        if old is not None and cur is not None and old != cur:
            add(
                Issue(
                    "bone_parent_changed",
                    SEVERITY_WARNING,
                    f"ボーン「{n}」の親が変わっています（{old or '(なし)'} → {cur or '(なし)'}）。"
                    "親ボーンオフセットの意味が変わっている可能性があります",
                    name=n,
                )
            )


# --- 可動域 ---


def _check_limits(doc: Document, profile: Optional[NamingProfile], add) -> None:
    sources = []
    if doc.limits:
        sources.append(("ドキュメント", doc.limits))
    if profile is not None and profile.limits:
        sources.append((f"プロファイル「{profile.name}」", profile.limits))
    for label, limits in sources:
        for name, (lo, hi) in limits.items():
            if not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi:
                add(
                    Issue(
                        "limit_invalid",
                        SEVERITY_ERROR,
                        f"{label}の可動域 {name} = [{_fmt(lo)}, {_fmt(hi)}] が正しくありません（最小 ≦ 最大）",
                        name=name,
                    )
                )
    for li, layer, rc, pt in _iter_points(doc):
        for name, w in pt.pose.curves.items():
            lo, hi = effective_limit(doc, profile, name)
            if w < lo - _LIMIT_TOL or w > hi + _LIMIT_TOL:
                tip = ""
                if not has_limit(doc, profile, name) and w > DEFAULT_LIMIT[1]:
                    tip = "（誇張で 1 を超えるなら limits に上限を指定してください）"
                add(
                    Issue(
                        "limit_exceeded",
                        SEVERITY_WARNING,
                        f"{_where(li, doc, rc)} の {name} = {_fmt(w)} が可動域 [{_fmt(lo)}, {_fmt(hi)}] の外です{tip}",
                        layer=li,
                        row=rc[0],
                        col=rc[1],
                        name=name,
                    )
                )


def _check_excluded(doc: Document, add) -> None:
    """UE 版（R-17）: 補正除外パターンに一致するのにポーズへ保存されている（ベイクで無視される）。"""
    found: dict[tuple[str, str], tuple[int, tuple[int, int]]] = {}
    for li, layer, rc, pt in _iter_points(doc):
        for n, w in pt.pose.curves.items():
            if abs(w) > 1e-6 and is_mirror_excluded(n, doc.exclude.curves):
                found.setdefault((KIND_CURVE, n), (li, rc))
        for n in pt.pose.bones:
            if is_mirror_excluded(n, doc.exclude.bones):
                found.setdefault((KIND_BONE, n), (li, rc))
    for (kind, n), (li, rc) in found.items():
        label = "シェイプ" if kind == KIND_CURVE else "ボーン"
        add(
            Issue(
                "excluded_in_pose",
                SEVERITY_INFO,
                f"補正除外パターンに一致する{label}「{n}」がポーズに含まれています（ベイク時に無視されます）",
                layer=li,
                row=rc[0],
                col=rc[1],
                name=n,
            )
        )


# --- ベイク・ターゲット ---


def _bake_candidates(doc: Document):
    """ベイクの対象になる点: 格子の中で、ポーズが空でないもの。"""
    for li, layer, rc, pt in _iter_points(doc):
        if _in_grid(doc, rc) and not pt.pose.is_empty():
            yield li, layer, rc, pt


def _check_bake(doc: Document, scene: SceneInfo, bake_state: Optional[Mapping[str, str]], add, bake_exclude: Optional[Mapping[str, str]] = None) -> None:
    if bake_state is None:
        return
    cur_sig = exclude_signature(doc) if bake_exclude else ""
    targets = set(scene.targets) if scene.targets is not None else None
    for li, layer, rc, pt in _bake_candidates(doc):
        morph = naming.morph_name(doc.asset or "", layer.name, rc[0], rc[1])
        base = dict(layer=li, row=rc[0], col=rc[1], name=morph)
        if morph not in bake_state:
            add(Issue("point_unbaked", SEVERITY_INFO, f"未ベイクの点: {_where(li, doc, rc)}", **base))
            continue
        if targets is not None and morph not in targets:
            add(
                Issue(
                    "baked_morph_missing",
                    SEVERITY_WARNING,
                    f"ベイク済みのはずの {morph} がモデルにありません（再インポートで消えた可能性。再ベイクしてください）",
                    **base,
                )
            )
            continue
        if bake_state[morph] != pose_hash(pt.pose):
            add(
                Issue(
                    "point_changed_since_bake",
                    SEVERITY_WARNING,
                    f"ベイク後に変更された点: {_where(li, doc, rc)}（再ベイクしてください）",
                    **base,
                )
            )
            continue
        if bake_exclude and bake_exclude.get(morph, cur_sig) != cur_sig:
            add(
                Issue(
                    "point_changed_since_bake",
                    SEVERITY_WARNING,
                    f"補正から除外するものを変えたあと、焼き直していません: {_where(li, doc, rc)}（再ベイクしてください）",
                    **base,
                )
            )
            continue
        # 誇張用 `_Ex`（重み 1 超）: 要るのに無い = 誇張を足す前に焼いた / `_Ex` が消えた
        if needs_extreme(doc, li, rc):
            ex = naming.morph_name(doc.asset or "", layer.name, rc[0], rc[1], extreme=True)
            if ex not in bake_state or (targets is not None and ex not in targets) or bake_state[ex] != bake_state[morph]:
                add(
                    Issue(
                        "point_changed_since_bake",
                        SEVERITY_WARNING,
                        f"誇張用シェイプ（重み 1 超の分）が未ベイクの点: {_where(li, doc, rc)}（再ベイクしてください）",
                        layer=li,
                        row=rc[0],
                        col=rc[1],
                        name=ex,
                    )
                )


def _check_targets(doc: Document, scene: SceneInfo, add) -> None:
    if scene.targets is None:
        return
    asset = doc.asset or ""
    live = {(layer.name, rc[0], rc[1]) for _li, layer, rc, _pt in _bake_candidates(doc)}
    live_ex = {(layer.name, rc[0], rc[1]) for li, layer, rc, _pt in _bake_candidates(doc) if needs_extreme(doc, li, rc)}
    n_persp = len(doc.perspective.keys) if doc.perspective is not None else 0
    layer_names = [layer.name for layer in doc.layers]
    for t in sorted(scene.targets):
        verdict, p = naming.owner_of(t, asset, layer_names)
        if verdict in (naming.OWNER_FOREIGN, naming.OWNER_OTHER):
            continue  # 別のアセットの FC_*（アセット ID がより長いものを含む）、または FC_ でない名前は対象外（C-1）
        orphan = False
        why = ""
        if p is None:
            orphan, why = True, "名前の規則に合いません"
        elif p.kind == naming.KIND_POINT:
            if (p.layer, p.row, p.col) not in live:
                orphan, why = True, "格子に対応する点がありません"
        elif p.kind == naming.KIND_POINT_EX:
            if (p.layer, p.row, p.col) not in live:
                orphan, why = True, "格子に対応する点がありません"
            elif (p.layer, p.row, p.col) not in live_ex:
                orphan, why = True, "その点に重み 1 を超えるシェイプがありません（誇張が要らなくなりました）"
        elif p.kind == naming.KIND_PERSP:
            if (p.index or 0) >= n_persp:
                orphan, why = True, "対応するパース補正のキーがありません"
        if orphan:
            add(
                Issue(
                    "orphan_target",
                    SEVERITY_WARNING,
                    f"孤立したターゲット {t}（{why}）。ベイクで掃除されます",
                    name=t,
                )
            )

    # 彫り用の補助シェイプ（fcs_*）でどのポーズからも使われていないもの
    prefix = doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX
    if prefix and not prefix.startswith(naming.FC_PREFIX):
        used = {n for _li, _layer, _rc, pt in _iter_points(doc) for n in pt.pose.curves}
        used |= set(doc.working_set.curves)
        for t in sorted(scene.targets):
            if naming.is_combo_name(t, prefix):  # 組み合わせ補正は 2 つのシェイプで駆動される（ポーズからは使わない）ので「未使用」ではない
                continue
            if naming.is_sculpt_name(t, prefix) and not any(u == t or u.endswith("." + t) for u in used):
                add(
                    Issue(
                        "sculpt_unused",
                        SEVERITY_INFO,
                        f"どのポーズからも使われていない彫り用シェイプ {t}",
                        name=t,
                    )
                )

    for t, info in scene.target_info.items():
        if naming.is_fc_name(t) and info.empty:
            add(Issue("target_empty", SEVERITY_INFO, f"差分が 1 つも無いターゲット {t}", name=t))


# ---------------------------------------------------------------------------
# 一括改名（UE 版 FFacialReferenceRepair::ApplyCurveRenames / ApplyBoneRenames）
# ---------------------------------------------------------------------------


@dataclass
class RenameReport:
    """改名の結果の件数（UE 版 FFacialRepairReport と同じ区分）。"""

    curve_replacements: int = 0  # 全レイヤー・全点のシェイプの重み
    bone_replacements: int = 0  # 全レイヤー・全点のボーンオフセット
    working_set_replacements: int = 0  # 作業セット
    policy_replacements: int = 0  # 強度源・感情カーブ・ミラー / 補正の除外・基準ボーン・可動域
    collisions: int = 0  # 改名先が同じポーズ（辞書）内の別の名前と衝突し、値を失わないため見送った件数

    @property
    def total(self) -> int:
        return (
            self.curve_replacements
            + self.bone_replacements
            + self.working_set_replacements
            + self.policy_replacements
        )


def _clean_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    return {o: n for o, n in mapping.items() if o and n and o != n}


def _rename_dict(d: dict, mapping: Mapping[str, str]) -> tuple[dict, int, int]:
    """辞書のキーを一斉に改名する。最終的な名前が重なる改名は見送る（値を失わない。連鎖 A→B・C→A でも）。

    見送った改名のキーは元の名前のまま残り、その名前も「使われている」ので、ほかの改名がそこへ来るなら、それも見送る
    （重なりがなくなるまで繰り返す）。戻り値 = (新しい辞書, 改名数, 見送り数)。"""
    active = {k: mapping[k] for k in d if k in mapping and mapping[k] != k}
    collided = 0
    while True:
        finals = Counter(active.get(k, k) for k in d)
        clash = [k for k, f in active.items() if finals[f] > 1]
        if not clash:
            break
        for k in clash:
            del active[k]
        collided += len(clash)
    out: dict = {}
    for k, v in d.items():
        out[active.get(k, k)] = v
    return out, len(active), collided


def _rename_list(items: list[str], mapping: Mapping[str, str]) -> tuple[list[str], int]:
    """リストの要素を改名。改名で同じ名前が重なったら 2 つ目以降は落とす。戻り値 = (新しいリスト, 改名数)。"""
    out: list[str] = []
    replaced = 0
    for x in items:
        y = mapping.get(x, x)
        if y != x:
            replaced += 1
        if y not in out:
            out.append(y)
    return out, replaced


def rename_report(doc: Document, mapping: Mapping[str, str], kind: str) -> RenameReport:
    """`rename_references` と同じ（Document をその場で書き換える）が、区分けした件数を返す。

    kind = "curve" | "bone"。mapping = 旧名 → 新名（同じ・空は無視。同時に適用するので連鎖しない）。
    書き換える場所: 全レイヤー・全点のポーズ、作業セット、強度源カーブ・感情カーブ・doc.limits（curve のとき）、
    基準ボーン（bone のとき）、mirror.exclude と exclude.curves / bones の「文字列が完全一致」するもの（部分一致用の語は触らない）。
    """
    if kind not in (KIND_CURVE, KIND_BONE):
        raise ValueError(f'kind は "curve" か "bone": {kind!r}')
    rep = RenameReport()
    mp = _clean_mapping(mapping)
    if not mp:
        return rep

    for _li, _layer, _rc, pt in _iter_points(doc):
        if kind == KIND_CURVE:
            pt.pose.curves, n, c = _rename_dict(pt.pose.curves, mp)
            rep.curve_replacements += n
        else:
            pt.pose.bones, n, c = _rename_dict(pt.pose.bones, mp)
            rep.bone_replacements += n
        rep.collisions += c

    ws = doc.working_set
    if kind == KIND_CURVE:
        ws.curves, n = _rename_list(ws.curves, mp)
        rep.working_set_replacements += n
        doc.intensity_curves, n = _rename_list(doc.intensity_curves, mp)
        rep.policy_replacements += n
        for layer in doc.layers:
            if layer.emotion_curve in mp:
                layer.emotion_curve = mp[layer.emotion_curve]
                rep.policy_replacements += 1
        if doc.limits:
            doc.limits, n, c = _rename_dict(doc.limits, mp)
            rep.policy_replacements += n
            rep.collisions += c
        doc.exclude.curves, n = _rename_list(doc.exclude.curves, mp)
        rep.policy_replacements += n
    else:
        ws.bones, n = _rename_list(ws.bones, mp)
        rep.working_set_replacements += n
        if doc.grid.base_bone in mp:
            doc.grid.base_bone = mp[doc.grid.base_bone]
            rep.policy_replacements += 1
        doc.exclude.bones, n = _rename_list(doc.exclude.bones, mp)
        rep.policy_replacements += n
    doc.mirror.exclude, n = _rename_list(doc.mirror.exclude, mp)
    rep.policy_replacements += n
    return rep


def rename_references(doc: Document, mapping: Mapping[str, str], kind: str) -> int:
    """シェイプ（kind="curve"）またはボーン（kind="bone"）の名前を一括で改名し、置き換えた数を返す。内訳は rename_report。"""
    return rename_report(doc, mapping, kind).total


def remove_missing_references(doc: Document, scene_info: SceneInfo, include_case_mismatch: bool = False) -> int:
    """モデルに無いシェイプ・ボーンへの参照（全点のポーズ・作業セット・強度源カーブ）を消し、消した数を返す。

    scene_info.curves / bones が None の側は何もしない。大小違いだけの名前は、改名で直すべきなので既定では残す
    （include_case_mismatch=True で一緒に消す）。基準ボーン・感情カーブ・限界値・除外パターンは触らない。
    """
    removed = 0

    def gone(name: str, avail: set[str], pool: list[str]) -> bool:
        if name in avail:
            return False
        return include_case_mismatch or not case_only_match(name, pool)

    if scene_info.curves is not None:
        avail = set(scene_info.curves)
        pool = sorted(avail)
        for _li, _layer, _rc, pt in _iter_points(doc):
            dead = [n for n in pt.pose.curves if gone(n, avail, pool)]
            for n in dead:
                del pt.pose.curves[n]
            removed += len(dead)
        for attr_owner, attr in ((doc.working_set, "curves"), (doc, "intensity_curves")):
            cur = getattr(attr_owner, attr)
            keep = [n for n in cur if not gone(n, avail, pool)]
            removed += len(cur) - len(keep)
            setattr(attr_owner, attr, keep)
    if scene_info.bones is not None:
        avail = set(scene_info.bones)
        pool = sorted(avail)
        for _li, _layer, _rc, pt in _iter_points(doc):
            dead = [n for n in pt.pose.bones if gone(n, avail, pool)]
            for n in dead:
                del pt.pose.bones[n]
            removed += len(dead)
        keep = [n for n in doc.working_set.bones if not gone(n, avail, pool)]
        removed += len(doc.working_set.bones) - len(keep)
        doc.working_set.bones = keep
    return removed


__all__: Sequence[str] = (
    "Issue",
    "SceneInfo",
    "TargetInfo",
    "RenameReport",
    "validate",
    "pose_hash",
    "exclude_signature",
    "normalize_exclude",
    "suggest_names",
    "case_only_match",
    "levenshtein",
    "rename_references",
    "rename_report",
    "remove_missing_references",
)
