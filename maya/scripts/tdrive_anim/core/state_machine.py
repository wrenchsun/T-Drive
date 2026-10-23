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
    """MS2026 の `AC_PlayerPreview.controller` と同じ状態図（Idle / Run / Jump、遷移の条件と並び順も同じ、0.08 秒、Exit Time なし）。

    Idle / Run から ActionState==2 と ==3（Fall）で Jump へ。Jump からは「ActionState が 2 でも 3 でもなく Speed<0.1」で Idle、「…Speed>0.1」で Run。

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

    def cond(param: str, op: str, value: float) -> dict[str, Any]:
        return {"param": param, "op": op, "value": value}

    def tr(src: str, dst: str, *conds: dict[str, Any]) -> dict[str, Any]:
        return {"from": src, "to": dst, "duration": d, "conditions": list(conds)}

    jump, fall = ACTION_STATES["Jump"], ACTION_STATES["Fall"]
    not_airborne = [cond("ActionState", "!=", jump), cond("ActionState", "!=", fall)]
    # AC_PlayerPreview.controller と同じ（m_AnyStateTransitions は空。並び順も同じ。Exit Time なし・割り込みなし）
    return {
        "format": FORMAT, "version": VERSION,
        "parameters": [{"name": "Speed", "type": "float", "default": 0}, {"name": "ActionState", "type": "int", "default": 0}],
        "states": [{"name": "Idle", "clip": pick("Idle")}, {"name": "Run", "clip": pick("Run")}, {"name": "Jump", "clip": pick("Jump")}],
        "transitions": [
            tr("Idle", "Run", cond("Speed", ">", 0.1)),
            tr("Idle", "Jump", cond("ActionState", "==", jump)),
            tr("Idle", "Jump", cond("ActionState", "==", fall)),
            tr("Run", "Idle", cond("Speed", "<", 0.1)),
            tr("Run", "Jump", cond("ActionState", "==", jump)),
            tr("Run", "Jump", cond("ActionState", "==", fall)),
            tr("Jump", "Idle", *not_airborne, cond("Speed", "<", 0.1)),
            tr("Jump", "Run", *not_airborne, cond("Speed", ">", 0.1)),
        ],
    }


def validate(graph: dict[str, Any], clips: Iterable[dict[str, Any]] = ()) -> list[str]:
    """問題の一覧（空なら OK）。"""
    out: list[str] = []
    if not isinstance(graph, dict) or graph.get("format") != FORMAT:
        return [f"状態図ではありません（format が {FORMAT} ではない）"]

    def dicts(key: str) -> list[dict[str, Any]]:
        v = graph.get(key)
        if v is None:
            return []
        if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
            out.append(f"{key} はオブジェクトの配列にしてください")
            return []
        return v

    names = {c.get("name") for c in clips if isinstance(c, dict)}
    params = {p.get("name") for p in dicts("parameters")}
    states = {s.get("name"): s for s in dicts("states")}
    if not states and not out:
        out.append("状態がありません")
    for n, s in states.items():
        if not s.get("clip"):
            out.append(f"状態 {n} にクリップが割り当てられていません")
        elif names and s["clip"] not in names:
            out.append(f"状態 {n} のクリップ {s['clip']} が animset にありません")
    for i, t in enumerate(dicts("transitions")):
        where = f"遷移 {i + 1}（{t.get('from')} → {t.get('to')}）"
        if t.get("from") != ANY and t.get("from") not in states:
            out.append(f"{where}: 元の状態がありません")
        if t.get("to") not in states:
            out.append(f"{where}: 先の状態がありません")
        d = t.get("duration", DEFAULT_DURATION)
        if isinstance(d, bool) or not isinstance(d, (int, float)) or d < 0:
            out.append(f"{where}: 長さは 0 以上の秒")
        conds = t.get("conditions") or []
        if not isinstance(conds, list) or not all(isinstance(c, dict) for c in conds):
            out.append(f"{where}: conditions はオブジェクトの配列にしてください")
            continue
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


def state_at(result: Result, t: float) -> str:
    """時刻 t の状態の名前（遷移が始まった時刻から、行き先の状態とみなす）。"""
    cur = result.first_state
    for sw in result.switches:
        if sw["t"] <= t + 1e-9:
            cur = sw["to"]
    return cur


def describe_switches(result: Result) -> str:
    """評価した遷移の列を一行に（例: `0.00s Idle → 1.23s Run（0.08s）→ 3.00s Jump（0.08s）`）。"""
    parts = [f"0.00s {result.first_state}"]
    parts += [f"{sw['t']:.2f}s {sw['to']}（{sw['duration']:g}s）" for sw in result.switches]
    return " → ".join(parts)


MAX_TIMELINE_ROWS = 50
Timeline = list[tuple[float, dict[str, float]]]


def markers(timeline: Timeline) -> list[tuple[float, str, float]]:
    """時系列 → マーカーの列 (時刻, パラメータ, 値)。時刻順・同じ時刻ではパラメータ名順（タイムラインの表示用。AN-F-29）。"""
    return sorted(((t, p, float(v)) for t, vals in timeline for p, v in vals.items()), key=lambda m: (m[0], m[1]))


def delete_marker(timeline: Timeline, param: str, t: float) -> Timeline:
    """時刻 t のパラメータ param の記録を消した新しい時系列（そのイベントが空になれば、イベントごと消える）。"""
    out: Timeline = []
    for tt, vals in timeline:
        if abs(tt - t) <= 1e-6:
            vals = {k: v for k, v in vals.items() if k != param}
            if not vals:
                continue
        out.append((tt, dict(vals)))
    return out


def move_marker(timeline: Timeline, param: str, t_old: float, t_new: float) -> Timeline:
    """パラメータ param の記録を t_old から t_new へ動かした新しい時系列（行き先に同じパラメータがあれば上書き）。"""
    value = next((vals[param] for tt, vals in timeline if abs(tt - t_old) <= 1e-6 and param in vals), None)
    if value is None or abs(t_old - t_new) <= 1e-6:
        return [(tt, dict(v)) for tt, v in timeline]
    return with_event(delete_marker(timeline, param, t_old), t_new, {param: value})


def state_bands(result: Result, total_sec: float) -> list[tuple[float, float, str]]:
    """評価結果の状態の区間 (開始, 終了, 状態)。遷移が始まった時刻から行き先の状態とみなす。"""
    cuts = [(0.0, result.first_state)] + [(float(sw["t"]), sw["to"]) for sw in result.switches]
    return [(t0, cuts[i + 1][0] if i + 1 < len(cuts) else float(total_sec), name) for i, (t0, name) in enumerate(cuts)]


def frame_label(t: float, fps: int = 30) -> str:
    """時刻の表示（フレームと秒の両方。例: `f=90（3.00s）`）。"""
    return f"f={round(t * fps)}（{t:.2f}s）"


def marker_rows(timeline: Timeline, result: Result, fps: int = 30) -> list[tuple[str, str, str, str]]:
    """記録の表の行: (時刻, パラメータ, 値, その時刻の状態)。最大 `MAX_TIMELINE_ROWS` 行で、古い記録は先頭の 1 行（「古い記録 N 件」）に畳む。"""
    rows = [(frame_label(t, fps), p, f"{v:g}", state_at(result, t)) for t, p, v in markers(timeline)]
    if len(rows) > MAX_TIMELINE_ROWS:
        hidden = len(rows) - (MAX_TIMELINE_ROWS - 1)
        rows = [("…", f"古い記録 {hidden} 件", "", "")] + rows[hidden:]
    return rows


def with_event(timeline: list[tuple[float, dict[str, float]]], t: float, values: dict[str, float]) -> list[tuple[float, dict[str, float]]]:
    """時刻 t にパラメータの変更を足した新しい時系列（同じ時刻の変更は上書き）。"""
    out = [(tt, dict(v)) for tt, v in timeline if abs(tt - t) > 1e-9]
    same = next((v for tt, v in timeline if abs(tt - t) <= 1e-9), {})
    out.append((t, {**same, **values}))
    return sorted(out, key=lambda e: e[0])


def copy_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(graph)
