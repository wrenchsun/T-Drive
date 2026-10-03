"""土台の表情の計算（Maya 非依存。docs/14 §5.4）。

土台の表情 = 「この表情のとき補正がどう見えるか」を確かめるための、**シーンだけに当てる下敷き**（シェイプの重みだけ）。
ゲームでは表情アニメがシェイプを動かし、その上にベイクした FC_*（補正）が足される。シーンでも同じになるよう、
**土台の値 + ポーズの値**をシーンへ当てる（加算）。

- 共通のシェイプ: 土台 + ポーズ（可動域を超えても丸めずに当て、`over_limit` で報告する）
- 土台だけのシェイプ: 土台の値
- ポーズだけのシェイプ: ポーズの値
- 取り込み（`subtract`）はその逆: シーンの値 − 土台の値
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

EPS = 1e-6  # これ未満の値・超過は 0 とみなす

Limit = tuple[float, float]


@dataclass
class Layered:
    """土台 + ポーズを重ねた結果。"""

    weights: dict[str, float] = field(default_factory=dict)  # シーンへ当てる値（丸めない）
    shared: dict[str, float] = field(default_factory=dict)  # 土台とポーズの両方にあるシェイプ → 土台の値（画面の「+0.60（土台）」）
    base_only: list[str] = field(default_factory=list)  # 土台にしかない（ポーズの値が無い）シェイプ
    over_limit: dict[str, float] = field(default_factory=dict)  # 可動域を超えた（または下回った）シェイプ → 当てた値


def layer(
    pose_curves: Mapping[str, float],
    base_curves: Mapping[str, float],
    limit_of: Callable[[str], Limit],
) -> Layered:
    """ポーズと土台を重ねる。`base_curves` が空ならポーズのまま。

    shared は「ポーズに 0 でない値があり、土台にも値があるシェイプ」。ポーズが 0（または無い）シェイプは base_only に入る。
    """
    out = Layered(weights={k: float(v) for k, v in pose_curves.items()})
    for name, bv in base_curves.items():
        bv = float(bv)
        pv = float(pose_curves.get(name, 0.0))
        if abs(pv) > EPS:
            out.shared[name] = bv
        else:
            out.base_only.append(name)
        total = bv + pv
        out.weights[name] = total
        lo, hi = limit_of(name)
        if lo > hi:
            lo, hi = hi, lo
        if total > hi + EPS or total < lo - EPS:
            out.over_limit[name] = total
    out.base_only.sort()
    return out


def subtract(
    scene_curves: Mapping[str, float],
    base_curves: Mapping[str, float],
    skip: Iterable[str] = (),
) -> tuple[dict[str, float], list[str]]:
    """シーンの値から土台を引いて、ポーズの値にする。(ポーズの値, 土台のとおりで取り込まなかったシェイプ) を返す。

    `scene_curves` は 0 でないシェイプだけ（無いものは 0）。`skip` の名前（除外・作業セットの外）は引かずに、そのまま出さない
    （呼ぶ側が編集中の値を残す）。引いた結果が EPS 未満なら捨てる。
    """
    skipped = set(skip)
    out = {n: float(v) for n, v in scene_curves.items() if n not in base_curves and n not in skipped}
    ignored: list[str] = []
    for name, bv in base_curves.items():
        if name in skipped:
            continue
        d = float(scene_curves.get(name, 0.0)) - float(bv)
        if abs(d) < EPS:
            ignored.append(name)
        else:
            out[name] = d
    return out, sorted(ignored)
