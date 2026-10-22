"""遷移列 → 配置の計算（docs/20 §4.2。Maya 非依存）。"""

import pytest

from tdrive_anim.core import animset as A
from tdrive_anim.core import sequence as S

FPS = 30


def clips():
    d = A.new_animset("X")
    for n, a, b in (("Idle", 0, 60), ("Run_F", 100, 118), ("Jump", 200, 230)):
        A.add_clip(d, A.default_clip(f"A_X_{n}", a, b, "X"))
    return {c["name"]: c for c in d["clips"]}


def test_rows_to_placements_hold_and_crossfade():
    rows = [{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Run_F", "holdSec": 1.5, "crossFadeSec": 0.08}, {"clip": "A_X_Jump"}]
    p = S.from_rows(rows, clips(), FPS)
    assert [x.clip for x in p] == ["A_X_Idle", "A_X_Run_F", "A_X_Jump"]
    assert p[0].start == 0 and p[0].fade_in == 0
    assert p[1].start == 30 and p[1].fade_in == pytest.approx(0.08 * 30)  # 2.4 フレーム
    assert p[2].start == 30 + 45 and p[2].fade_in == pytest.approx(0.1 * 30)  # 既定 0.1 秒
    assert p[0].end == pytest.approx(30 + 2.4)  # 次のフェードが終わるまで影響がある
    assert p[2].end == pytest.approx(75 + 30)  # 最後は Jump の長さ（30 フレーム）
    assert S.total_frames(p) == pytest.approx(105)


def test_switch_at_belongs_to_the_row_switched_to():
    """SP-3: D-Drive の Steps と同じで、行 k の切替位置は「前のクリップがその位置に達したら行 k へ切り替える」（切り替え先の行が持つ）。"""
    c = clips()
    rows = [{"clip": "A_X_Run_F"}, {"clip": "A_X_Idle", "crossFadeSec": 0.3, "switchAt": 0.5}, {"clip": "A_X_Jump", "switchAt": 1.0}]
    p = S.from_rows(rows, c, FPS)
    assert p[1].start == 9  # 2 行目の切替位置 0.5 → 1 行目（Run は 18 フレーム）の半分で切り替わる
    assert p[2].start == 9 + 60  # 3 行目の切替位置 1 以上 → 2 行目（Idle は 60 フレーム）が終わってから
    # 自分の行の切替位置は、自分から次へ移る時刻には効かない
    p1 = S.from_rows([{"clip": "A_X_Run_F", "switchAt": 0.5}, {"clip": "A_X_Idle"}], c, FPS)
    assert p1[1].start == 18
    # holdSec が優先
    p2 = S.from_rows([{"clip": "A_X_Run_F", "holdSec": 1.0}, {"clip": "A_X_Idle", "switchAt": 0.5}], c, FPS)
    assert p2[1].start == 30


def test_speed():
    c = clips()
    p = S.from_rows([{"clip": "A_X_Run_F", "speed": 2.0}, {"clip": "A_X_Idle"}], c, FPS)
    assert p[0].length == 9 and p[1].start == 9
    assert S.source_frame(p[0], 4.5) == 109  # 2 倍速で 4.5 フレーム → ソースの 9 フレーム目
    with pytest.raises(S.SequenceError):
        S.from_rows([{"clip": "A_X_Run_F", "speed": 0}], c, FPS)


def test_errors():
    with pytest.raises(S.SequenceError):
        S.from_rows([{"clip": "nope"}], clips(), FPS)
    assert S.from_rows([], clips(), FPS) == [] and S.total_frames([]) == 0


def test_source_frame_loop_and_hold():
    c = clips()
    p = S.from_rows([{"clip": "A_X_Run_F", "holdSec": 2.0}, {"clip": "A_X_Jump"}], c, FPS)
    run = p[0]  # ループ（Run）
    assert S.source_frame(run, 0) == 100 and S.source_frame(run, 18) == 100 and S.source_frame(run, 20) == 102
    p2 = S.from_rows([{"clip": "A_X_Jump", "holdSec": 2.0}], c, FPS)
    assert S.source_frame(p2[0], 100) == 230  # ループでないクリップは最後で止まる


def test_pose_weights_crossfade_is_linear_and_sums_to_one():
    c = clips()
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Run_F", "holdSec": 1.0, "crossFadeSec": 0.1}], c, FPS)
    assert S.pose_weights(p, 10) == [("A_X_Idle", 1.0)]
    assert S.pose_weights(p, 30) == [("A_X_Idle", 1.0)]  # 切り替えの瞬間（重み 0）
    w = dict(S.pose_weights(p, 31.5))  # フェード（3 フレーム）の半分
    assert w["A_X_Idle"] == pytest.approx(0.5) and w["A_X_Run_F"] == pytest.approx(0.5)
    assert S.pose_weights(p, 33) == [("A_X_Run_F", 1.0)]
    assert S.pose_weights(p, 60) == [("A_X_Run_F", 1.0)]


def test_zero_fade_is_a_cut():
    c = clips()
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Jump", "crossFadeSec": 0}], c, FPS)
    assert S.pose_weights(p, 29.9) == [("A_X_Idle", 1.0)]
    assert S.pose_weights(p, 30) == [("A_X_Jump", 1.0)]


def test_three_way_weights():
    c = clips()
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 0.1}, {"clip": "A_X_Run_F", "holdSec": 0.1, "crossFadeSec": 0.2},
                     {"clip": "A_X_Jump", "crossFadeSec": 0.2}], c, FPS)
    w = S.pose_weights(p, 3 + 3)  # 2 つ目の途中で 3 つ目のフェードが始まる
    assert sum(x for _, x in w) == pytest.approx(1.0)


def test_crossfade_is_linear_at_quarter_points():
    """TS-1: 線形（smoothstep などでは通らない）: フェードの 1/4 で 0.25、3/4 で 0.75。"""
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Run_F", "holdSec": 1.0, "crossFadeSec": 0.2}], clips(), FPS)  # 開始 30、6 フレームでフェード
    w = dict(S.pose_weights(p, 31.5))
    assert w["A_X_Run_F"] == pytest.approx(0.25) and w["A_X_Idle"] == pytest.approx(0.75)
    w = dict(S.pose_weights(p, 34.5))
    assert w["A_X_Run_F"] == pytest.approx(0.75) and w["A_X_Idle"] == pytest.approx(0.25)


def test_three_way_weights_by_hand():
    """TS-1: 3 本の重なりの各重みを手計算の値で（連鎖 lerp(lerp(A,B),C) の展開）。Idle 3f → Run（3f から 6f で）→ Jump（6f から 6f で）。"""
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 0.1}, {"clip": "A_X_Run_F", "holdSec": 0.1, "crossFadeSec": 0.2},
                     {"clip": "A_X_Jump", "crossFadeSec": 0.2}], clips(), FPS)
    assert [round(x.start, 6) for x in p] == [0, 3, 6]
    w = dict(S.pose_weights(p, 7.5))  # Run の重み 0.75、Jump の重み 0.25
    assert w["A_X_Jump"] == pytest.approx(0.25) and w["A_X_Run_F"] == pytest.approx(0.75 * 0.75) and w["A_X_Idle"] == pytest.approx(0.25 * 0.75)
    w = dict(S.pose_weights(p, 9))  # Run のフェードは終わり（1.0）、Jump は半分
    assert w == {"A_X_Jump": pytest.approx(0.5), "A_X_Run_F": pytest.approx(0.5)}


def test_cross_fade_in_fixed_time_semantics():
    """TS-2: Unity の CrossFadeInFixedTime と同じ: 次のクリップは先頭から、出ていく側もフェード中は進み続ける（ループは巡回、非ループは最後で止まる）。"""
    c = clips()
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Run_F", "holdSec": 1.0, "crossFadeSec": 0.2}], c, FPS)
    assert S.source_frame(p[1], 30) == 100  # 次のクリップは切り替えの瞬間に先頭から
    assert S.source_frame(p[1], 36) == 106  # 6 フレーム進む
    assert S.source_frame(p[0], 33) == 33  # 出ていく側（Idle、ループ）はフェード中も進む（30 で止まらない）
    q = S.from_rows([{"clip": "A_X_Jump", "holdSec": 2.0}, {"clip": "A_X_Idle", "crossFadeSec": 0.5}], c, FPS)  # Jump は 60 フレーム見せる（30 フレームの非ループ）
    assert S.source_frame(q[0], 45) == 230  # 非ループは最後のフレームで止まったまま混ざる
    assert S.source_frame(q[0], 70) == 230


def test_row_crossfade_defaults_to_the_clip_attribute():
    """SP-4: 行に crossFadeSec が無ければクリップの crossFade（無ければ 0.1）。"""
    c = clips()
    c["A_X_Run_F"]["crossFade"] = 0.08
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Run_F"}], c, FPS)
    assert p[1].fade_in == pytest.approx(0.08 * FPS)
    p = S.from_rows([{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Run_F", "crossFadeSec": 0.3}], c, FPS)  # 行の値が優先
    assert p[1].fade_in == pytest.approx(0.3 * FPS)
    rows = S.default_rows(list(c.values()), "X")
    assert rows[0]["crossFadeSec"] == pytest.approx(c["A_X_Idle"]["crossFade"])


def test_overlap_warning_when_next_row_starts_before_fade_ends():
    """SP-6: 行の間隔がフェードより短いとき（lerp(lerp(A,B),C)）は警告。"""
    c = clips()
    ok = S.from_rows([{"clip": "A_X_Idle", "holdSec": 1.0}, {"clip": "A_X_Run_F", "holdSec": 1.0}, {"clip": "A_X_Jump"}], c, FPS)
    assert S.overlap_warnings(ok, FPS) == []
    bad = S.from_rows([{"clip": "A_X_Idle", "holdSec": 0.1}, {"clip": "A_X_Run_F", "holdSec": 0.1, "crossFadeSec": 0.2}, {"clip": "A_X_Jump", "crossFadeSec": 0.2}], c, FPS)
    w = S.overlap_warnings(bad, FPS)
    assert len(w) == 1 and "3 行目" in w[0]
