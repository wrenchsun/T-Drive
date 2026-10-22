"""遷移列 → 配置（開始・重なり・速度）の計算（docs/20 §4.2。Maya 非依存）。

手で並べた行（`rows`）からも、状態図の評価結果（`core/state_machine.py`）からも、同じ `Placement` の列を作る。
Maya 側の合成（`composition.py`）はこの配置だけを見る。

## 行（D-Drive の AnimEditor「Blend」の Steps と同じ約束）
`{"clip": 名前, "holdSec": 0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0}`

- 1 行目が最初に再生されるクリップ（`crossFadeSec` は使わない）
- `crossFadeSec` = **この行へ切り替わるときの**固定秒・線形のクロスフェード（Unity の `CrossFadeInFixedTime` と同じ）
- 次の行への切り替えを始める時刻（この行の開始からの時間）:
  `holdSec > 0` ならその秒数（ループするクリップを指定秒だけ見せる）。0 なら `switchAt` × クリップの長さ（1 以上 = クリップの最後）
  — D-Drive は「SwitchAt = 前のクリップの切替位置 0〜1、1 以上は終了時」。holdSec が優先（docs/20 に追記）
- `speed` = 再生速度（D-Drive の SetSpeed 相当。1 = そのまま）

## 時間の単位
配置は**フレーム**（シーンの時間単位）で持つ。秒は `fps` で換算する。クリップの長さ = (end - start) / speed フレーム
（範囲は end を含むので、最終フレームに着くまでの長さ）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_CROSSFADE = 0.1


class SequenceError(ValueError):
    pass


@dataclass
class Placement:
    clip: str
    start: float  # タイムラインでの開始フレーム
    fade_in: float  # 前の配置から切り替わるフレーム数（0 = 即切り替え）
    src_start: int
    src_end: int
    loop: bool
    speed: float = 1.0
    end: float = 0.0  # この配置が影響を持つ最後のフレーム（次の配置のフェードが終わるまで）

    @property
    def length(self) -> float:
        """最初のフレームから最後のフレームまでの長さ（フレーム）。"""
        return (self.src_end - self.src_start) / self.speed


def _info(clips: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    c = clips.get(name)
    if c is None:
        raise SequenceError(f"クリップがありません: {name}")
    return c


def _place(clip: dict[str, Any], start: float, fade_in: float, speed: float) -> Placement:
    if speed <= 0:
        raise SequenceError("速度は 0 より大きい値にしてください")
    return Placement(clip["name"], start, fade_in, int(clip["start"]), int(clip["end"]), bool(clip["loop"]), speed)


def _speed(row: dict[str, Any]) -> float:
    v = row.get("speed")
    speed = 1.0 if v is None else float(v)
    if speed <= 0:
        raise SequenceError("速度は 0 より大きい値にしてください")
    return speed


def switch_frames(row: dict[str, Any], clip: dict[str, Any], fps: int) -> float:
    """この行の開始から、次の行への切り替えを始めるまでのフレーム数。"""
    speed = _speed(row)
    length = (int(clip["end"]) - int(clip["start"])) / speed
    hold = float(row.get("holdSec", 0) or 0)
    if hold > 0:
        return hold * fps
    at = float(row.get("switchAt", 1.0) if row.get("switchAt") is not None else 1.0)
    return length if at >= 1.0 else max(0.0, at) * length


def from_rows(rows: list[dict[str, Any]], clips: dict[str, dict[str, Any]], fps: int) -> list[Placement]:
    """行 → 配置。行が空なら空。存在しないクリップ・0 以下の速度は SequenceError。"""
    out: list[Placement] = []
    t = 0.0
    for i, row in enumerate(rows):
        clip = _info(clips, row["clip"])
        fade = 0.0 if i == 0 else float(row.get("crossFadeSec", DEFAULT_CROSSFADE)) * fps
        p = _place(clip, t, fade, _speed(row))
        out.append(p)
        t += switch_frames(row, clip, fps)
    _finish(out, rows, clips, fps)
    return out


def _finish(placements: list[Placement], rows: list[dict[str, Any]], clips: dict[str, dict[str, Any]], fps: int) -> None:
    """各配置の影響の終わり（次の配置のフェードが終わる時刻。最後は自分の切り替え時刻）を入れる。"""
    for i, p in enumerate(placements):
        if i + 1 < len(placements):
            nxt = placements[i + 1]
            p.end = nxt.start + nxt.fade_in
        else:
            p.end = p.start + switch_frames(rows[i], _info(clips, p.clip), fps)


def from_switches(first: str, switches: list[dict[str, Any]], clips: dict[str, dict[str, Any]], fps: int, total_sec: float) -> list[Placement]:
    """状態図の評価結果（切り替えの時刻列）→ 配置。

    `switches` = `[{"t": 秒, "to": クリップ名, "duration": 秒}]`（時刻順）。最初のクリップは 0 秒から。
    """
    out = [_place(_info(clips, first), 0.0, 0.0, 1.0)]
    for sw in switches:
        out.append(_place(_info(clips, sw["to"]), sw["t"] * fps, max(0.0, float(sw["duration"])) * fps, 1.0))
    for i, p in enumerate(out):
        p.end = out[i + 1].start + out[i + 1].fade_in if i + 1 < len(out) else max(total_sec * fps, p.start)
    return out


def total_frames(placements: Iterable[Placement]) -> float:
    return max((p.end for p in placements), default=0.0)


# ------------------------------------------------------------------ 評価（テスト・Maya 側の確認用）
def source_frame(p: Placement, t: float) -> float:
    """タイムラインの時刻 t（フレーム）に配置 p が再生しているソースのフレーム。ループは範囲で巡回、ループでなければ最後で止まる。"""
    span = p.src_end - p.src_start
    local = max(0.0, (t - p.start)) * p.speed
    if span <= 0:
        return float(p.src_start)
    if p.loop:
        local = local % span
    else:
        local = min(local, span)
    return p.src_start + local


def fade_weight(p: Placement, t: float) -> float:
    """配置 p が前の結果へ混ざる重み（0〜1。線形）。最初の配置は常に 1。"""
    if p.fade_in <= 0:
        return 1.0 if t >= p.start else 0.0
    return min(1.0, max(0.0, (t - p.start) / p.fade_in))


def pose_weights(placements: list[Placement], t: float) -> list[tuple[str, float]]:
    """時刻 t の最終的な寄与（クリップ名, 重み）。連鎖 R_k = lerp(R_{k-1}, C_k, w_k) を展開したもの。重みの合計は 1。"""
    n = len(placements)
    ws = [fade_weight(p, t) if i else 1.0 for i, p in enumerate(placements)]
    out = []
    for j, p in enumerate(placements):
        w = ws[j]
        for i in range(j + 1, n):
            w *= 1.0 - ws[i]
        if w > 1e-9:
            out.append((p.clip, w))
    return out


# ------------------------------------------------------------------ 既定の行
def default_rows(clips: Iterable[dict[str, Any]], model_id: str = "") -> list[dict[str, Any]]:
    """遷移列の初期値: Idle 1.0 秒 → Run 1.5 秒 → Jump（docs/20 §4.2 の例）。クリップは動作名で探す（Run は `_F` を優先）。無いものは飛ばす。"""
    from . import animset as A

    clips = list(clips)

    def pick(action: str) -> str:
        found = sorted((c["name"] for c in clips if A.action_of(c["name"], model_id) == action), key=lambda n: (not n.endswith("_F"), n))
        return found[0] if found else ""

    plan = (("Idle", {"holdSec": 1.0}), ("Run", {"holdSec": 1.5, "crossFadeSec": 0.08}), ("Jump", {"crossFadeSec": 0.08}))
    rows = []
    for action, extra in plan:
        name = pick(action)
        if name:
            rows.append({"clip": name, "holdSec": 0.0, "crossFadeSec": DEFAULT_CROSSFADE, "switchAt": 1.0, "speed": 1.0, **extra})
    return rows
