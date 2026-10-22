"""状態図の評価（docs/20 §4.2。Maya 非依存）。MS2026 の `AC_PlayerPreview` と同じ動きを Maya で確かめるための計算。

状態図（`animset.json` の `stateGraph`）:

    { "format": "TDriveStateGraph", "version": 1,
      "parameters": [{"name": "Speed", "type": "float", "default": 0}, {"name": "ActionState", "type": "int", "default": 0}],
      "states": [{"name": "Idle", "clip": "A_X_Idle"}, ...],
      "transitions": [{"from": "Idle" | "*", "to": "Run", "duration": 0.08, "conditions": [{"param": "Speed", "op": ">", "value": 0.1}]}] }

- 遷移は**固定秒・線形のクロスフェード**で **Exit Time なし**（条件が成り立ったその時刻に始まる）。`from: "*"` は Any State
  （どの状態からでも。自分自身へは遷移しない）。同じ時刻に複数の遷移が成り立つときは、Any State が先、あとは並び順（Unity と同じ）
- 条件の演算子: `>` `<` `>=` `<=` `==` `!=`。条件が空の遷移は常に成り立つ（Exit Time のない無条件遷移は作れないので検証で警告）
- 遷移の途中では新しい遷移を始めない（Unity の既定: Interruption なし）
- `evaluate` は**フレーム刻み**で状態を進める。パラメータの時系列は `[(秒, {パラメータ: 値}), ...]`（時刻順。その時刻以降その値）
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import sequence

FORMAT = "TDriveStateGraph"
VERSION = 1
OPS = (">", "<", ">=", "<=", "==", "!=")
ANY = "*"
DEFAULT_DURATION = 0.08  # MS2026 の AC_PlayerPreview の遷移（固定秒）

# MS2026 の PlayerActionState（docs/20 §0.2）
ACTION_STATES = {"Idle": 0, "Move": 1, "Jump": 2, "Fall": 3, "Interacting": 4, "Stunned": 5}


class StateGraphError(ValueError):
    pass


def default_graph(clips: Iterable[dict[str, Any]], model_id: str = "") -> dict[str, Any]:
    """MS2026 の `AC_PlayerPreview` と同じ状態図（Idle / Run / Jump、0.08 秒、Exit Time なし）。

    状態に割り当てるクリップは、動作名（Idle / Run / Jump）で探す（Run は `_F` が先、無ければ最初に見つかったもの）。
    見つからない状態はクリップ名が空のまま残す（検証で知らせる）。
    """
    from . import animset as A

    clips = list(clips)

    def pick(action: str) -> str:
        found = [c["name"] for c in clips if A.action_of(c["name"], model_id) == action]
        found.sort(key=lambda n: (not n.endswith("_F"), n))
        return found[0] if found else ""

    d = DEFAULT_DURATION
    return {
        "format": FORMAT, "version": VERSION,
        "parameters": [{"name": "Speed", "type": "float", "default": 0}, {"name": "ActionState", "type": "int", "default": 0}],
        "states": [{"name": "Idle", "clip": pick("Idle")}, {"name": "Run", "clip": pick("Run")}, {"name": "Jump", "clip": pick("Jump")}],
        "transitions": [
            {"from": "Idle", "to": "Run", "duration": d, "conditions": [{"param": "Speed", "op": ">", "value": 0.1}]},
            {"from": "Run", "to": "Idle", "duration": d, "conditions": [{"param": "Speed", "op": "<", "value": 0.1}]},
            {"from": ANY, "to": "Jump", "duration": d, "conditions": [{"param": "ActionState", "op": "==", "value": ACTION_STATES["Jump"]}]},
            {"from": "Jump", "to": "Idle", "duration": d, "conditions": [{"param": "ActionState", "op": "!=", "value": ACTION_STATES["Jump"]}]},
        ],
    }


def validate(graph: dict[str, Any], clips: Iterable[dict[str, Any]] = ()) -> list[str]:
    """問題の一覧（空なら OK）。"""
    out: list[str] = []
    if not isinstance(graph, dict) or graph.get("format") != FORMAT:
        return [f"状態図ではありません（format が {FORMAT} ではない）"]
    names = {c["name"] for c in clips}
    params = {p.get("name") for p in graph.get("parameters", [])}
    states = {s.get("name"): s for s in graph.get("states", [])}
    if not states:
        out.append("状態がありません")
    for n, s in states.items():
        if not s.get("clip"):
            out.append(f"状態 {n} にクリップが割り当てられていません")
        elif names and s["clip"] not in names:
            out.append(f"状態 {n} のクリップ {s['clip']} が animset にありません")
    for i, t in enumerate(graph.get("transitions", [])):
        where = f"遷移 {i + 1}（{t.get('from')} → {t.get('to')}）"
        if t.get("from") != ANY and t.get("from") not in states:
            out.append(f"{where}: 元の状態がありません")
        if t.get("to") not in states:
            out.append(f"{where}: 先の状態がありません")
        d = t.get("duration", DEFAULT_DURATION)
        if isinstance(d, bool) or not isinstance(d, (int, float)) or d < 0:
            out.append(f"{where}: 長さは 0 以上の秒")
        conds = t.get("conditions") or []
        if not conds:
            out.append(f"{where}: 条件がありません（すぐに遷移し続けます）")
        for c in conds:
            if c.get("param") not in params:
                out.append(f"{where}: パラメータ {c.get('param')} がありません")
            if c.get("op") not in OPS:
                out.append(f"{where}: 演算子 {c.get('op')} は使えません（{' '.join(OPS)}）")
    return out


def _holds(value: float, op: str, ref: float) -> bool:
    return {">": value > ref, "<": value < ref, ">=": value >= ref, "<=": value <= ref, "==": value == ref, "!=": value != ref}[op]


def conditions_met(conds: list[dict[str, Any]], params: dict[str, float]) -> bool:
    return all(_holds(float(params.get(c["param"], 0)), c["op"], float(c["value"])) for c in conds)


@dataclass
class Result:
    switches: list[dict[str, Any]] = field(default_factory=list)  # [{"t": 秒, "from": 状態, "to": 状態, "clip": クリップ名, "duration": 秒}]
    first_state: str = ""
    first_clip: str = ""
    total_sec: float = 0.0


def initial_params(graph: dict[str, Any]) -> dict[str, float]:
    return {p["name"]: float(p.get("default", 0)) for p in graph.get("parameters", [])}


def evaluate(graph: dict[str, Any], timeline: list[tuple[float, dict[str, float]]], total_sec: float, fps: int = 30,
             first_state: str | None = None) -> Result:
    """パラメータの時系列から、状態の切り替えの時刻列を求める。

    最初の状態は `first_state`、無ければ状態の 1 つ目。0 秒より前のパラメータは既定値。
    """
    errs = [e for e in validate(graph) if "クリップ" not in e]  # クリップ未割り当ては評価の妨げにならない
    if errs:
        raise StateGraphError(errs[0])
    states = {s["name"]: s for s in graph["states"]}
    cur = first_state or graph["states"][0]["name"]
    if cur not in states:
        raise StateGraphError(f"最初の状態がありません: {cur}")
    params = initial_params(graph)
    events = sorted(timeline, key=lambda e: e[0])
    ei = 0
    res = Result(first_state=cur, first_clip=states[cur].get("clip", ""), total_sec=total_sec)
    busy_until = -1.0  # 遷移の途中（この時刻まで新しい遷移を始めない）
    frames = int(round(total_sec * fps))
    any_first = [t for t in graph["transitions"] if t["from"] == ANY] + [t for t in graph["transitions"] if t["from"] != ANY]
    for f in range(frames + 1):
        t = f / fps
        while ei < len(events) and events[ei][0] <= t + 1e-9:
            params.update({k: float(v) for k, v in events[ei][1].items()})
            ei += 1
        if t < busy_until - 1e-9:
            continue
        for tr in any_first:
            if tr["from"] not in (ANY, cur) or tr["to"] == cur:
                continue
            if tr["from"] == ANY or tr["from"] == cur:
                if conditions_met(tr.get("conditions") or [], params):
                    dur = float(tr.get("duration", DEFAULT_DURATION))
                    res.switches.append({"t": t, "from": cur, "to": tr["to"], "clip": states[tr["to"]].get("clip", ""), "duration": dur})
                    cur = tr["to"]
                    busy_until = t + dur
                    break
    return res


def to_placements(result: Result, clips: dict[str, dict[str, Any]], fps: int) -> list[sequence.Placement]:
    """評価結果 → 配置。クリップが割り当てられていない状態があれば SequenceError。"""
    if not result.first_clip:
        raise sequence.SequenceError(f"状態 {result.first_state} にクリップが割り当てられていません")
    for sw in result.switches:
        if not sw["clip"]:
            raise sequence.SequenceError(f"状態 {sw['to']} にクリップが割り当てられていません")
    return sequence.from_switches(result.first_clip, [{"t": s["t"], "to": s["clip"], "duration": s["duration"]} for s in result.switches],
                                  clips, fps, result.total_sec)


def with_event(timeline: list[tuple[float, dict[str, float]]], t: float, values: dict[str, float]) -> list[tuple[float, dict[str, float]]]:
    """時刻 t にパラメータの変更を足した新しい時系列（同じ時刻の変更は上書き）。"""
    out = [(tt, dict(v)) for tt, v in timeline if abs(tt - t) > 1e-9]
    same = next((v for tt, v in timeline if abs(tt - t) <= 1e-9), {})
    out.append((t, {**same, **values}))
    return sorted(out, key=lambda e: e[0])


def copy_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(graph)
