"""ブレンドツリーの重み計算（docs/20 §4.5、AN-11-1。Maya 非依存）。"""

import math

import pytest

from tdrive_anim.core import animset as A
from tdrive_anim.core import blend_tree as BT
from tdrive_anim.core import state_machine as M

SQ = math.sqrt(0.5)


def tree1d():
    return {"type": "1D", "parameters": ["Speed"], "motions": [
        {"clip": "A_X_Idle", "threshold": 0}, {"clip": "A_X_Walk_F", "threshold": 0.5}, {"clip": "A_X_Run_F", "threshold": 1}]}


def tree2d(kind="2DSimpleDirectional"):
    return {"type": kind, "parameters": ["MoveX", "MoveY"], "motions": [
        {"clip": "A_X_Idle", "position": [0, 0]}, {"clip": "A_X_Walk_F", "position": [0, 1]}, {"clip": "A_X_Walk_B", "position": [0, -1]},
        {"clip": "A_X_Walk_L", "position": [-1, 0]}, {"clip": "A_X_Walk_R", "position": [1, 0]}]}


def close(ws, expect):
    assert ws == pytest.approx(expect, abs=1e-9)


def test_1d_on_motion_midpoint_and_clamp():
    t = tree1d()
    close(BT.weights(t, [0]), [1, 0, 0])
    close(BT.weights(t, [0.5]), [0, 1, 0])
    close(BT.weights(t, [0.25]), [0.5, 0.5, 0])
    close(BT.weights(t, [0.75]), [0, 0.5, 0.5])
    close(BT.weights(t, [-3]), [1, 0, 0])
    close(BT.weights(t, [9]), [0, 0, 1])


def test_1d_unsorted_thresholds():
    t = tree1d()
    t["motions"].reverse()
    close(BT.weights(t, [0.25]), [0, 0.5, 0.5])


def test_simple_directional_origin_and_motions():
    t = tree2d()
    close(BT.weights(t, [0, 0]), [1, 0, 0, 0, 0])
    close(BT.weights(t, [0, 1]), [0, 1, 0, 0, 0])
    close(BT.weights(t, [-1, 0]), [0, 0, 0, 1, 0])
    close(BT.weights(t, [0, 0.5]), [0.5, 0.5, 0, 0, 0])
    close(BT.weights(t, [0.5, 0]), [0.5, 0, 0, 0, 0.5])


def test_simple_directional_between_neighbours_and_outside():
    t = tree2d()
    close(BT.weights(t, [0.5, 0.5]), [0, 0.5, 0, 0, 0.5])  # F と R の間、大きさ 1 に届く点では中央は 0
    close(BT.weights(t, [0.25, 0.25]), [0.5, 0.25, 0, 0, 0.25])
    close(BT.weights(t, [3, 3]), [0, 0.5, 0, 0, 0.5])  # 外側は正規化（中央 0）
    close(BT.weights(t, [-0.5, -0.5]), [0, 0, 0.5, 0.5, 0])


def test_simple_directional_without_center_normalizes():
    t = tree2d()
    t["motions"] = t["motions"][1:]
    close(BT.weights(t, [0, 0.4]), [1, 0, 0, 0])
    close(BT.weights(t, [0.2, 0.2]), [0.5, 0, 0, 0.5])


@pytest.mark.parametrize("kind", ["2DFreeformCartesian", "2DFreeformDirectional"])
def test_freeform_on_motion_is_one(kind):
    t = tree2d(kind)
    for i, m in enumerate(t["motions"]):
        ws = BT.weights(t, m["position"])
        close(ws, [1.0 if k == i else 0.0 for k in range(5)])


def test_freeform_cartesian_fixed_values():
    t = {"type": "2DFreeformCartesian", "parameters": ["x", "y"], "motions": [
        {"clip": "a", "position": [0, 0]}, {"clip": "b", "position": [1, 0]}]}
    close(BT.weights(t, [0.5, 0]), [0.5, 0.5])
    close(BT.weights(t, [0.25, 0]), [0.75, 0.25])
    close(BT.weights(t, [-5, 0]), [1, 0])  # 帯の外は端のモーション
    sq = {"type": "2DFreeformCartesian", "parameters": ["x", "y"], "motions": [
        {"clip": c, "position": p} for c, p in (("a", [0, 0]), ("b", [1, 0]), ("c", [0, 1]), ("d", [1, 1]))]}
    ws = BT.weights(sq, [0.5, 0.5])
    assert sum(ws) == pytest.approx(1.0)
    assert ws[0] == pytest.approx(ws[3]) and ws[1] == pytest.approx(ws[2])


def test_freeform_directional_angle_midpoint():
    t = {"type": "2DFreeformDirectional", "parameters": ["x", "y"], "motions": [
        {"clip": "f", "position": [0, 1]}, {"clip": "r", "position": [1, 0]}]}
    close(BT.weights(t, [SQ, SQ]), [0.5, 0.5])
    ws = BT.weights(t, [0.2, 0.98])
    assert ws[0] > ws[1] > 0


def test_weights_sum_to_one_everywhere():
    for kind in BT.TYPES[1:]:
        t = tree2d(kind)
        for x in (-1.5, -0.6, 0, 0.3, 0.9, 1.7):
            for y in (-1.2, -0.3, 0, 0.55, 1.4):
                ws = BT.weights(t, [x, y])
                assert sum(ws) == pytest.approx(1.0), (kind, x, y)
                assert all(w >= -1e-12 for w in ws)


def test_validate():
    assert BT.validate(tree1d()) == []
    assert BT.validate(tree2d()) == []
    assert BT.validate({"type": "3D"}) != []
    t = tree1d()
    t["motions"][1]["threshold"] = 0
    assert any("重なって" in e for e in BT.validate(t))
    t = tree2d()
    t["motions"][2]["position"] = [0, 1]
    assert any("重なって" in e for e in BT.validate(t))
    t = tree2d()
    t["parameters"] = ["MoveX"]
    assert BT.validate(t) != []
    assert any("animset にありません" in e for e in BT.validate(tree1d(), [{"name": "A_X_Idle"}]))
    assert any("状態図にありません" in e for e in BT.validate(tree1d(), params=["Foo"]))
    with pytest.raises(BT.BlendTreeError):
        BT.weights({"type": "1D", "parameters": ["S"], "motions": []}, [0])


def test_templates():
    d = A.new_animset("X")
    for n in ("Idle", "Walk_F", "Walk_B", "Walk_L", "Walk_R", "Run_F"):
        A.add_clip(d, A.default_clip(f"A_X_{n}", 0, 30, "X"))
    t1, t2 = BT.template_1d(d["clips"], "X"), BT.template_2d(d["clips"], "X")
    assert [m["threshold"] for m in t1["motions"]] == [0, 0.5, 1] and t1["motions"][2]["clip"] == "A_X_Run_F"
    assert t2["motions"][0] == {"clip": "A_X_Idle", "position": [0, 0]} and len(t2["motions"]) == 5
    assert BT.validate(t1, d["clips"]) == [] and BT.validate(t2, d["clips"]) == []
    assert BT.template_2d([{"name": "A_X_Idle"}], "X") is None
    assert BT.default_range(t2) == (-1.0, 1.0) and BT.default_range(t1) == (0.0, 1.0)
    assert BT.weight_text(t1, [0.3, 0.7, 0]) == "A_X_Walk_F 70% / A_X_Idle 30%"


def test_state_graph_accepts_blend_tree_state():
    d = A.new_animset("X")
    for n in ("Idle", "Run_F", "Jump", "Walk_F", "Walk_B"):
        A.add_clip(d, A.default_clip(f"A_X_{n}", 0, 30, "X"))
    g = M.default_graph(d["clips"], "X")
    g["parameters"] += [{"name": "MoveX", "type": "float", "default": 0}, {"name": "MoveY", "type": "float", "default": 0}]
    g["states"].append({"name": "Move", "blendTree": BT.template_2d(d["clips"], "X")})
    assert M.validate(g, d["clips"]) == []
    g["states"][-1]["blendTree"]["parameters"] = ["Nope", "MoveY"]
    assert any("Nope" in e for e in M.validate(g, d["clips"]))


def test_animset_roundtrip_keeps_blend_tree(tmp_path):
    """状態図の blendTree は animset.json にそのまま保存され、読み込みで戻る（Unity 用の animset.json の入力。AN-11-3）。"""
    d = A.new_animset("X")
    for n in ("Idle", "Run_F", "Jump", "Walk_F", "Walk_B"):
        A.add_clip(d, A.default_clip(f"A_X_{n}", 0, 30, "X"))
    g = M.default_graph(d["clips"], "X")
    g["parameters"] += [{"name": "MoveX", "type": "float", "default": 0}, {"name": "MoveY", "type": "float", "default": 0}]
    g["states"].append({"name": "Move", "blendTree": BT.template_2d(d["clips"], "X")})
    d["stateGraph"] = g
    path = tmp_path / "X.animset.json"
    A.save(path, d)
    back = A.load(path)
    assert back["stateGraph"]["states"][-1]["blendTree"] == g["states"][-1]["blendTree"]
    assert M.validate(back["stateGraph"], back["clips"]) == []
    assert A.errors(back) == []
