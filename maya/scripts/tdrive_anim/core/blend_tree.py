"""ブレンドツリーの重み計算（docs/20 §4.5。Maya 非依存）。Unity の Blend Tree と同じ 4 種類。

状態図 JSON の状態に持たせる（MINOR）:

    { "name": "Move", "blendTree": { "type": "2DSimpleDirectional", "parameters": ["MoveX", "MoveY"],
        "motions": [ {"clip": "A_X_Idle", "position": [0, 0]}, {"clip": "A_X_Walk_F", "position": [0, 1]}, ... ] } }
    { "name": "Locomotion", "blendTree": { "type": "1D", "parameters": ["Speed"],
        "motions": [ {"clip": "A_X_Idle", "threshold": 0}, {"clip": "A_X_Run_F", "threshold": 1} ] } }

- `1D`: 隣り合う 2 つのしきい値の間で線形。範囲外は端のモーションだけ
- `2DSimpleDirectional`: サンプルの角度で隣の 2 モーションを選び、三角形（原点・モーション i・モーション j）の重心座標を重みにする。
  原点のモーション（中央）が残りを埋める。重心座標の合計が 1 を超えたら正規化して中央は 0。中央が無ければ 2 つで正規化
- `2DFreeformCartesian` / `2DFreeformDirectional`: gradient band interpolation（Rune Skovbo Johansen の論文）。
  モーション i の重み = 他のすべての j について `h_ij = clamp(1 - dot(p_is, p_ij) / |p_ij|², 0, 1)` の最小値。全体で正規化。
  Directional は位置を (長さ, 角度) として、`p_ij = ((l_j - l_i) / ((l_i + l_j) / 2), 角度(i→j))`、`p_is` も同じ正規化で（長さ 0 のモーションがあるときの角度差は 0）

`Cycle Offset` と `Mirror` は対象外。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

TYPES = ("1D", "2DSimpleDirectional", "2DFreeformDirectional", "2DFreeformCartesian")
EPS = 1e-6


class BlendTreeError(ValueError):
    pass


def is_2d(tree: dict[str, Any]) -> bool:
    return tree.get("type") != "1D"


def motion_pos(m: dict[str, Any]) -> tuple[float, float]:
    p = m.get("position") or [0, 0]
    return float(p[0]), float(p[1])


def validate(tree: Any, clips: Iterable[dict[str, Any]] = (), params: Iterable[str] | None = None) -> list[str]:
    """問題の一覧（空なら OK）。clips を渡すとクリップの存在、params を渡すとパラメータ名が状態図にあるかも見る。"""
    if not isinstance(tree, dict):
        return ["blendTree はオブジェクトにしてください"]
    out: list[str] = []
    typ = tree.get("type")
    if typ not in TYPES:
        return [f"blendTree の type は {' / '.join(TYPES)} のどれか（{typ!r}）"]
    n_params = 1 if typ == "1D" else 2
    ps = tree.get("parameters")
    if not isinstance(ps, list) or len(ps) != n_params or not all(isinstance(p, str) and p for p in ps):
        out.append(f"{typ} の parameters は名前 {n_params} つの配列にしてください")
        ps = []
    elif params is not None:
        have = set(params)
        out += [f"パラメータ {p} が状態図にありません" for p in ps if p not in have]
    motions = tree.get("motions")
    if not isinstance(motions, list) or not motions or not all(isinstance(m, dict) for m in motions):
        return [*out, "motions はモーションの配列（1 つ以上）にしてください"]
    names = {c.get("name") for c in clips if isinstance(c, dict)}
    seen: list[Any] = []
    for i, m in enumerate(motions):
        where = f"モーション {i + 1}（{m.get('clip')}）"
        if not m.get("clip"):
            out.append(f"{where}: クリップ名がありません")
        elif names and m["clip"] not in names:
            out.append(f"{where}: クリップが animset にありません")
        if typ == "1D":
            t = m.get("threshold")
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                out.append(f"{where}: threshold は数値にしてください")
                continue
            key: Any = float(t)
        else:
            p = m.get("position")
            if not (isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in p)):
                out.append(f"{where}: position は [x, y] にしてください")
                continue
            key = (float(p[0]), float(p[1]))
        if any(_same(key, k) for k in seen):
            out.append(f"{where}: {'threshold' if typ == '1D' else 'position'} が他のモーションと重なっています")
        seen.append(key)
    return out


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, tuple):
        return abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) < EPS
    return abs(a - b) < EPS


def parameters(tree: dict[str, Any]) -> list[str]:
    return list(tree.get("parameters") or [])


# ------------------------------------------------------------------ 重み
def weights(tree: dict[str, Any], values: Sequence[float]) -> list[float]:
    """モーションごとの重み（motions と同じ並び。合計 1）。values は parameters と同じ順の値。"""
    errs = validate(tree)
    if errs:
        raise BlendTreeError(errs[0])
    motions = tree["motions"]
    typ = tree["type"]
    if typ == "1D":
        return _w1d([float(m["threshold"]) for m in motions], float(values[0]))
    pos = [motion_pos(m) for m in motions]
    s = (float(values[0]), float(values[1]))
    if typ == "2DSimpleDirectional":
        return _simple_directional(pos, s)
    return _freeform(pos, s, directional=typ == "2DFreeformDirectional")


def weights_by_name(tree: dict[str, Any], params: dict[str, float]) -> list[float]:
    return weights(tree, [float(params.get(p, 0.0)) for p in parameters(tree)])


def _w1d(th: list[float], x: float) -> list[float]:
    order = sorted(range(len(th)), key=lambda i: th[i])
    w = [0.0] * len(th)
    if x <= th[order[0]]:
        w[order[0]] = 1.0
    elif x >= th[order[-1]]:
        w[order[-1]] = 1.0
    else:
        for a, b in zip(order, order[1:]):
            if th[a] <= x <= th[b]:
                t = (x - th[a]) / (th[b] - th[a])
                w[a], w[b] = 1.0 - t, t
                break
    return w


def _simple_directional(pos: list[tuple[float, float]], s: tuple[float, float]) -> list[float]:
    n = len(pos)
    w = [0.0] * n
    centers = [i for i, p in enumerate(pos) if math.hypot(*p) < EPS]
    ring = sorted((i for i in range(n) if i not in centers), key=lambda i: math.atan2(pos[i][1], pos[i][0]))
    if not ring:  # 中央しか無い
        return [1.0 / n] * n
    slen = math.hypot(*s)

    def give_center(rest: float) -> None:
        for c in centers:
            w[c] = rest / len(centers)

    if slen < EPS:
        if centers:
            give_center(1.0)
        else:
            for i in ring:
                w[i] = 1.0 / len(ring)
        return w
    if len(ring) == 1:
        i = ring[0]
        p = pos[i]
        a = max(0.0, min(1.0, (s[0] * p[0] + s[1] * p[1]) / (p[0] ** 2 + p[1] ** 2)))
        if centers:
            w[i] = a
            give_center(1.0 - a)
        else:
            w[i] = 1.0
        return w
    theta = math.atan2(s[1], s[0])
    ang = [math.atan2(pos[i][1], pos[i][0]) for i in ring]
    # s の角度を挟む隣り合う 2 モーション（一周して戻る組を含む）
    k = len(ring)
    pair = None
    for idx in range(k):
        a0, a1 = ang[idx], ang[(idx + 1) % k]
        gap = (a1 - a0) % (2 * math.pi) if k > 1 else 2 * math.pi
        off = (theta - a0) % (2 * math.pi)
        if off <= gap + 1e-12:
            pair = (idx, (idx + 1) % k, gap, off)
            break
    i, j, gap, off = pair
    pi_, pj = pos[ring[i]], pos[ring[j]]
    det = pi_[0] * pj[1] - pi_[1] * pj[0]
    if gap < math.pi - 1e-9 and abs(det) > EPS:
        a = (s[0] * pj[1] - s[1] * pj[0]) / det
        b = (pi_[0] * s[1] - pi_[1] * s[0]) / det
    else:  # 隣の 2 本が 180° 以上開いている: 角度で線形に配り、大きさは平均の長さで頭打ち
        r = 0.0 if gap < EPS else off / gap
        mean = (math.hypot(*pi_) + math.hypot(*pj)) / 2
        mag = min(1.0, slen / mean)
        a, b = (1.0 - r) * mag, r * mag
    a, b = max(0.0, a), max(0.0, b)
    t = a + b
    if centers and t < 1.0:
        w[ring[i]], w[ring[j]] = a, b
        give_center(1.0 - t)
    else:
        t = t or 1.0
        w[ring[i]], w[ring[j]] = a / t, b / t
    return w


def _angle(a: tuple[float, float], b: tuple[float, float]) -> float:
    """a から b への符号付き角（ラジアン）。どちらかの長さが 0 なら 0。"""
    if math.hypot(*a) < EPS or math.hypot(*b) < EPS:
        return 0.0
    return math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])


def _freeform(pos: list[tuple[float, float]], s: tuple[float, float], directional: bool) -> list[float]:
    n = len(pos)
    if n == 1:
        return [1.0]
    w = []
    for i in range(n):
        wi = 1.0
        for j in range(n):
            if i == j:
                continue
            if directional:
                li, lj, ls = math.hypot(*pos[i]), math.hypot(*pos[j]), math.hypot(*s)
                norm = (li + lj) / 2
                pij = ((lj - li) / norm, _angle(pos[i], pos[j]))
                pis = ((ls - li) / norm, _angle(pos[i], s))
            else:
                pij = (pos[j][0] - pos[i][0], pos[j][1] - pos[i][1])
                pis = (s[0] - pos[i][0], s[1] - pos[i][1])
            d = pij[0] ** 2 + pij[1] ** 2
            h = 1.0 - (pis[0] * pij[0] + pis[1] * pij[1]) / d if d > EPS else 1.0
            wi = min(wi, max(0.0, min(1.0, h)))
        w.append(wi)
    total = sum(w)
    if total < EPS:  # どのバンドにも入らない: 一番近いモーション
        near = min(range(n), key=lambda i: (pos[i][0] - s[0]) ** 2 + (pos[i][1] - s[1]) ** 2)
        return [1.0 if i == near else 0.0 for i in range(n)]
    return [x / total for x in w]


# ------------------------------------------------------------------ 表示・雛形
def default_range(tree: dict[str, Any]) -> tuple[float, float]:
    """パッド / スライダーの既定の範囲（モーションの位置がすべて 0 以上なら 0〜1、そうでなければ -1〜1）。"""
    vals = [float(m["threshold"]) for m in tree["motions"]] if tree.get("type") == "1D" else [c for m in tree["motions"] for c in motion_pos(m)]
    return (0.0, 1.0) if all(v >= 0 for v in vals) and max(vals, default=1) <= 1 else (-1.0, 1.0)


def weight_text(tree: dict[str, Any], ws: Sequence[float]) -> str:
    """重みの一行表示（例: `Walk_F 70% / Idle 30%`）。0.5% 未満は出さない。"""
    parts = [(w, m["clip"]) for m, w in zip(tree["motions"], ws) if w >= 0.005]
    parts.sort(key=lambda x: -x[0])
    return " / ".join(f"{name} {w * 100:.0f}%" for w, name in parts)


def _find(clips: Iterable[dict[str, Any]], model_id: str, action: str, direction: str = "") -> str:
    from . import animset as A

    found = [c["name"] for c in clips if A.action_of(c["name"], model_id) == action and (not direction or c["name"].endswith("_" + direction))]
    return sorted(found)[0] if found else ""


def template_1d(clips: Iterable[dict[str, Any]], model_id: str = "") -> dict[str, Any] | None:
    """Idle(0) / Walk(0.5) / Run(1) の 1D Locomotion。Idle と Run が無ければ None（Walk は無くてもよい）。"""
    clips = list(clips)
    idle, walk, run = _find(clips, model_id, "Idle"), _find(clips, model_id, "Walk", "F") or _find(clips, model_id, "Walk"), _find(clips, model_id, "Run", "F") or _find(clips, model_id, "Run")
    if not (idle and run):
        return None
    motions = [{"clip": idle, "threshold": 0}] + ([{"clip": walk, "threshold": 0.5}] if walk else []) + [{"clip": run, "threshold": 1}]
    return {"type": "1D", "parameters": ["Speed"], "motions": motions}


def template_2d(clips: Iterable[dict[str, Any]], model_id: str = "") -> dict[str, Any] | None:
    """Walk の `_F/_B/_L/_R`（あれば Idle を中央）の 2DSimpleDirectional。2 方向以上そろわなければ None。"""
    clips = list(clips)
    dirs = {"F": (0, 1), "B": (0, -1), "L": (-1, 0), "R": (1, 0)}
    motions = []
    for d, p in dirs.items():
        name = _find(clips, model_id, "Walk", d) or _find(clips, model_id, "Run", d)
        if name:
            motions.append({"clip": name, "position": list(p)})
    if len(motions) < 2:
        return None
    idle = _find(clips, model_id, "Idle")
    if idle:
        motions.insert(0, {"clip": idle, "position": [0, 0]})
    return {"type": "2DSimpleDirectional", "parameters": ["MoveX", "MoveY"], "motions": motions}
