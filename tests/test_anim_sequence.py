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


def test_switch_at_uses_previous_clip_length():
    c = clips()
    rows = [{"clip": "A_X_Run_F", "switchAt": 0.5}, {"clip": "A_X_Idle", "crossFadeSec": 0.3, "switchAt": 1.0}, {"clip": "A_X_Jump"}]
    p = S.from_rows(rows, c, FPS)
    assert p[1].start == 9  # Run は 18 フレーム → 半分
    assert p[2].start == 9 + 60  # 1 以上は終了時（Idle は 60 フレーム）
    # holdSec が優先
    p2 = S.from_rows([{"clip": "A_X_Run_F", "switchAt": 0.5, "holdSec": 1.0}, {"clip": "A_X_Idle"}], c, FPS)
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
