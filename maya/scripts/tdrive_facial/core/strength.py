"""部位別の強さ（`bake.partStrength`。docs/14 §5.7、F5-6。Maya 非依存）。

ベイクのとき、ポーズのシェイプの重み・ボーンのずらしに、名前の部分一致で決まる強さ（0〜1）を掛ける。
- 一致の判定は補正の除外と同じ（`profile.is_mirror_excluded` と同じ部分一致・大文字小文字を区別・空のパターンは無視）
- 複数に当たるときは一覧の上にあるものが優先（最初に当たったものだけ）
- 補正から除外される名前は、強さに関わらず焼かれない（除外が先）。ここでは触らず、そのまま返す
- シェイプ: 重み × 強さ / ボーン: 移動 × 強さ、回転は恒等との球面補間、スケールは 1 との線形補間
"""

from __future__ import annotations

import math
from typing import Optional

from .model import BoneOffset, Document, PartStrength, SourcePose
from .profile import is_mirror_excluded


def effective_entries(doc: Document) -> list[tuple[str, float]]:
    """焼くときに効く (パターン, 強さ)。空のパターン・数でない強さは捨て、強さは 0〜1 に収める。順番は上が優先。"""
    if doc.bake is None:
        return []
    out: list[tuple[str, float]] = []
    for e in doc.bake.part_strength:
        if not e.pattern or not isinstance(e.strength, (int, float)) or not math.isfinite(e.strength):
            continue
        out.append((e.pattern, min(1.0, max(0.0, float(e.strength)))))
    return out


def strength_for(entries: list[tuple[str, float]], name: str) -> float:
    """名前に最初に当たったパターンの強さ。当たらなければ 1。"""
    for pattern, s in entries:
        if pattern in name:
            return s
    return 1.0


def _slerp_from_identity(q, s: float):
    """恒等の向きから q へ、割合 s の球面補間（短い側の経路。正規化済み）。"""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    x, y, z, w = x / n, y / n, z / n, w / n
    if w < 0.0:  # 反対側の表現なら裏返して短い経路にする
        x, y, z, w = -x, -y, -z, -w
    w = min(1.0, w)
    sin_half = math.sqrt(max(0.0, 1.0 - w * w))
    if sin_half < 1e-9:  # ほぼ恒等
        return (0.0, 0.0, 0.0, 1.0)
    theta = math.acos(w)
    k = math.sin(s * theta) / sin_half
    return (x * k, y * k, z * k, math.cos(s * theta))


def scale_bone(b: BoneOffset, s: float) -> BoneOffset:
    """ボーンのずらしに強さ s（0〜1）を掛けた複製。s = 1 は同じ値。"""
    if s >= 1.0:
        return BoneOffset(t=b.t, r=b.r, s=b.s, extra=dict(b.extra))
    return BoneOffset(
        t=tuple(v * s for v in b.t),
        r=_slerp_from_identity(b.r, s),
        s=tuple(1.0 + (v - 1.0) * s for v in b.s),
        extra=dict(b.extra),
    )


def scale_pose(doc: Document, pose: SourcePose) -> SourcePose:
    """ベイクに使うポーズ（部位別の強さを掛けたもの）。強さの指定が無ければ pose そのもの（コピーしない）。元のポーズは変えない。"""
    entries = effective_entries(doc)
    if not entries:
        return pose
    ex_c, ex_b = doc.exclude.curves, doc.exclude.bones
    out = SourcePose()
    for name, w in pose.curves.items():
        s = 1.0 if is_mirror_excluded(name, ex_c) else strength_for(entries, name)
        out.curves[name] = w * s if s != 1.0 else w
    for name, b in pose.bones.items():
        s = 1.0 if is_mirror_excluded(name, ex_b) else strength_for(entries, name)
        out.bones[name] = scale_bone(b, s) if s != 1.0 else b
    return out


def curve_factor(doc: Document, name: str) -> float:
    """シェイプの重みに掛かる強さ（除外されていれば 1。除外は別に効く）。"""
    entries = effective_entries(doc)
    if not entries or is_mirror_excluded(name, doc.exclude.curves):
        return 1.0
    return strength_for(entries, name)


def signature_part(doc: Document) -> str:
    """効く指定の指紋（空なら ""。順番も含める = 上が優先なので並べ替えで結果が変わる）。"""
    import hashlib
    import json

    entries = effective_entries(doc)
    if not entries:
        return ""
    norm = json.dumps([[p, round(s, 6)] for p, s in entries], ensure_ascii=False)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def entries_of(doc: Document) -> list[PartStrength]:
    """画面用: 設定そのまま（無ければ空）。"""
    return list(doc.bake.part_strength) if doc.bake is not None else []


# ---------------------------------------------------------------------------
# 一覧の編集（session が Undo の印を付けて呼ぶ。Document を直接書き換える。失敗のときは変えずに (code, 日本語の理由) を返す）
# ---------------------------------------------------------------------------

Failure = tuple[str, str]


def _bake(doc: Document):
    from .model import Bake

    if doc.bake is None:
        doc.bake = Bake()
    return doc.bake


def check_strength(value) -> Optional[Failure]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "strength_invalid", "強さは数で入れてください"
    if not 0.0 <= value <= 1.0:
        return "strength_range", "強さは 0〜1 で入れてください"
    return None


def add_entry(doc: Document, pattern: str, strength: float) -> Optional[Failure]:
    """一覧の末尾（いちばん優先度が低い）に足す。"""
    pattern = (pattern or "").strip()
    if not pattern:
        return "empty", "パターンが空です"
    bad = check_strength(strength)
    if bad:
        return bad
    bake = _bake(doc)
    if any(e.pattern == pattern for e in bake.part_strength):
        return "exists", f"「{pattern}」は既にあります"
    bake.part_strength.append(PartStrength(pattern=pattern, strength=float(strength)))
    return None


def remove_entry(doc: Document, index: int) -> Optional[Failure]:
    entries = _bake(doc).part_strength
    if not 0 <= index < len(entries):
        return "index", "その行はありません"
    del entries[index]
    return None


def set_entry_strength(doc: Document, index: int, strength: float) -> Optional[Failure]:
    entries = _bake(doc).part_strength
    if not 0 <= index < len(entries):
        return "index", "その行はありません"
    bad = check_strength(strength)
    if bad:
        return bad
    entries[index].strength = float(strength)
    return None


def move_entry(doc: Document, index: int, delta: int) -> Optional[Failure]:
    """delta = -1 で上へ（優先度が上がる）、+1 で下へ。端では何も変えない（失敗にもしない）。"""
    entries = _bake(doc).part_strength
    if not 0 <= index < len(entries):
        return "index", "その行はありません"
    j = index + delta
    if 0 <= j < len(entries):
        entries[index], entries[j] = entries[j], entries[index]
    return None
