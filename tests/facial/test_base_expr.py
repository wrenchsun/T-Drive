"""土台の表情の計算（core/base_expr.py。Maya なし）。加算・可動域の報告・取り込みで引く。"""

from tdrive_facial.core import base_expr


def lim(name):
    return (0.0, 2.0) if name == "wide" else (0.0, 1.0)


def test_layer_adds_base_to_pose_for_shared_shapes():
    r = base_expr.layer({"a": 0.5, "b": 0.2}, {"a": 0.6, "c": 0.4}, lim)
    assert abs(r.weights["a"] - 1.1) < 1e-9  # 共通: 土台 + ポーズ
    assert r.weights["b"] == 0.2  # ポーズだけ
    assert r.weights["c"] == 0.4  # 土台だけ
    assert r.shared == {"a": 0.6}
    assert r.base_only == ["c"]


def test_layer_does_not_clamp_and_reports_over_limit():
    r = base_expr.layer({"a": 0.9, "wide": 0.9}, {"a": 0.7, "wide": 0.7}, lim)
    assert abs(r.weights["a"] - 1.6) < 1e-9  # 丸めない
    assert set(r.over_limit) == {"a"}  # wide は 0〜2 なので範囲内
    assert abs(r.over_limit["a"] - 1.6) < 1e-9


def test_layer_base_alone_over_limit_and_negative():
    r = base_expr.layer({}, {"a": 1.3, "n": -0.2}, lim)
    assert set(r.over_limit) == {"a", "n"}
    assert r.shared == {} and r.base_only == ["a", "n"]


def test_layer_without_base_is_identity():
    r = base_expr.layer({"a": 0.5}, {}, lim)
    assert r.weights == {"a": 0.5} and not r.shared and not r.over_limit


def test_layer_zero_pose_value_counts_as_base_only():
    r = base_expr.layer({"a": 0.0}, {"a": 0.5}, lim)
    assert r.base_only == ["a"] and r.shared == {}


def test_subtract_inverts_layer():
    pose = {"a": 0.5, "b": 0.2}
    base = {"a": 0.6, "c": 0.4}
    scene = base_expr.layer(pose, base, lim).weights
    got, ignored = base_expr.subtract(scene, base)
    assert abs(got["a"] - 0.5) < 1e-9 and abs(got["b"] - 0.2) < 1e-9
    assert "c" not in got and ignored == ["c"]  # 土台だけのシェイプは取り込まない


def test_subtract_drops_tiny_and_keeps_changes_to_base_shapes():
    got, ignored = base_expr.subtract({"a": 0.6000001, "c": 0.9}, {"a": 0.6, "c": 0.4})
    assert "a" not in got and ignored == ["a"]
    assert abs(got["c"] - 0.5) < 1e-9  # 土台のシェイプをシーンで動かしたぶんは取り込む


def test_subtract_missing_scene_value_is_negative_delta():
    got, _ = base_expr.subtract({}, {"a": 0.5})
    assert abs(got["a"] + 0.5) < 1e-9  # シーンで 0 にした = ポーズが −0.5 を足して打ち消す


def test_subtract_skip_names_are_left_alone():
    got, ignored = base_expr.subtract({"x": 0.3}, {"a": 0.5, "x": 0.1}, skip=["a"])
    assert "a" not in got and "a" not in ignored
    assert abs(got["x"] - 0.2) < 1e-9
