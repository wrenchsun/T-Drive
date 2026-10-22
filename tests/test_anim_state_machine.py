"""状態図の評価（docs/20 §4.2。Maya 非依存）。MS2026 の AC_PlayerPreview と同じ動きになること。"""

import pytest

from tdrive_anim.core import animset as A
from tdrive_anim.core import sequence as S
from tdrive_anim.core import state_machine as M

FPS = 30


def make():
    d = A.new_animset("X")
    for n, a, b in (("Idle", 0, 60), ("Run_F", 100, 118), ("Jump", 200, 230), ("Run_B", 300, 318)):
        A.add_clip(d, A.default_clip(f"A_X_{n}", a, b, "X"))
    return d


def test_default_graph_matches_ac_player_preview():
    d = make()
    g = M.default_graph(d["clips"], "X")
    assert [s["name"] for s in g["states"]] == ["Idle", "Run", "Jump"]
    assert [s["clip"] for s in g["states"]] == ["A_X_Idle", "A_X_Run_F", "A_X_Jump"]  # Run は _F を優先
    assert all(t["duration"] == 0.08 for t in g["transitions"])
    assert M.validate(g, d["clips"]) == []


def test_idle_then_speed_goes_to_run_in_0_08s():
    d = make()
    g = M.default_graph(d["clips"], "X")
    r = M.evaluate(g, [(1.0, {"Speed": 0.5})], total_sec=3.0, fps=FPS)
    assert r.first_state == "Idle"
    assert len(r.switches) == 1
    sw = r.switches[0]
    assert (sw["from"], sw["to"], sw["duration"]) == ("Idle", "Run", 0.08)
    assert sw["t"] == pytest.approx(1.0)  # Exit Time なし: 条件が成り立った時刻ちょうど
    placements = M.to_placements(r, {c["name"]: c for c in d["clips"]}, FPS)
    assert [p.clip for p in placements] == ["A_X_Idle", "A_X_Run_F"]
    assert placements[1].start == pytest.approx(30) and placements[1].fade_in == pytest.approx(2.4)
    w = dict(S.pose_weights(placements, 31.2))  # フェードの半分
    assert w["A_X_Idle"] == pytest.approx(0.5)


def test_action_state_jump_from_any_state():
    d = make()
    g = M.default_graph(d["clips"], "X")
    # Run の最中に ActionState=2
    r = M.evaluate(g, [(0.5, {"Speed": 1.0}), (2.0, {"ActionState": 2})], total_sec=4.0, fps=FPS)
    assert [(s["from"], s["to"]) for s in r.switches] == [("Idle", "Run"), ("Run", "Jump")]
    assert r.switches[1]["t"] == pytest.approx(2.0)
    # Idle の最中でも
    r2 = M.evaluate(g, [(1.0, {"ActionState": 2})], total_sec=3.0, fps=FPS)
    assert [(s["from"], s["to"]) for s in r2.switches][:1] == [("Idle", "Jump")]


def test_jump_returns_to_idle_when_action_state_changes():
    g = M.default_graph(make()["clips"], "X")
    r = M.evaluate(g, [(1.0, {"ActionState": 2}), (2.0, {"ActionState": 0})], total_sec=4.0, fps=FPS)
    assert [(s["from"], s["to"]) for s in r.switches] == [("Idle", "Jump"), ("Jump", "Idle")]
    assert r.switches[1]["t"] == pytest.approx(2.0)


def test_no_interruption_during_transition_and_no_self_transition():
    g = M.default_graph(make()["clips"], "X")
    # Jump の遷移中（0.08 秒）に Speed を変えても、Jump に入ってから 0.08 秒は次の遷移が起きない。ActionState=2 のままなら Jump に留まる
    r = M.evaluate(g, [(1.0, {"ActionState": 2, "Speed": 1.0})], total_sec=3.0, fps=FPS)
    assert [(s["from"], s["to"]) for s in r.switches] == [("Idle", "Jump")]  # Any → Jump が自分自身へ繰り返さない


def test_speed_dead_zone_stays():
    g = M.default_graph(make()["clips"], "X")
    r = M.evaluate(g, [(1.0, {"Speed": 0.1})], total_sec=2.0, fps=FPS)  # ちょうど 0.1 は > でも < でもない
    assert r.switches == []


def test_parameter_values_persist_until_changed():
    g = M.default_graph(make()["clips"], "X")
    r = M.evaluate(g, [(0.5, {"Speed": 1.0}), (1.5, {"Speed": 0.0})], total_sec=3.0, fps=FPS)
    assert [(s["from"], s["to"]) for s in r.switches] == [("Idle", "Run"), ("Run", "Idle")]


def test_with_event_replaces_same_time():
    tl = M.with_event([], 1.0, {"Speed": 1.0})
    tl = M.with_event(tl, 1.0, {"ActionState": 2})
    tl = M.with_event(tl, 0.5, {"Speed": 0.3})
    assert tl == [(0.5, {"Speed": 0.3}), (1.0, {"Speed": 1.0, "ActionState": 2})]


def test_validate_reports_problems():
    d = make()
    g = M.default_graph(d["clips"], "X")
    g["states"][1]["clip"] = ""
    g["transitions"][0]["conditions"][0]["op"] = "=~"
    g["transitions"][1]["to"] = "Nowhere"
    g["transitions"][2]["conditions"] = []
    g["transitions"][3]["conditions"][0]["param"] = "Nope"
    msgs = M.validate(g, d["clips"])
    assert any("Run にクリップ" in m for m in msgs) and any("演算子" in m for m in msgs) and any("先の状態" in m for m in msgs)
    assert any("条件がありません" in m for m in msgs) and any("パラメータ Nope" in m for m in msgs)
    assert M.validate({"format": "x"}) != []
    with pytest.raises(M.StateGraphError):
        M.evaluate(g, [], 1.0)


def test_unassigned_clip_blocks_placements():
    d = make()
    g = M.default_graph([c for c in d["clips"] if "Jump" not in c["name"]], "X")  # Jump のクリップなし
    r = M.evaluate(g, [(1.0, {"ActionState": 2})], 3.0, FPS)
    with pytest.raises(S.SequenceError):
        M.to_placements(r, {c["name"]: c for c in d["clips"]}, FPS)
