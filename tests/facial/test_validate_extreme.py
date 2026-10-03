"""誇張（重み 1 超 → `_Ex`）・重みの出どころ・補正除外の検証（core/validate.py、Maya 非依存）。"""

from tdrive_facial.core import model as m
from tdrive_facial.core import validate as V

from test_validate import MORPH, make_doc, make_scene

EX = MORPH + "_Ex"


def codes(issues):
    return [i.code for i in issues]


def test_needs_extreme_and_helpers():
    doc = make_doc()
    pose = doc.layers[0].points[(1, 2)].pose
    assert not V.has_extreme(doc, pose) and not V.needs_extreme(doc, 0, (1, 2))
    pose.curves["bs.smile_L"] = 1.5
    assert V.extreme_curves(doc, pose) == ["bs.smile_L"] and V.needs_extreme(doc, 0, (1, 2))
    clamped = V.clamp_extreme(pose)
    assert clamped.curves["bs.smile_L"] == 1.0 and pose.curves["bs.smile_L"] == 1.5  # 元は変えない
    doc.exclude.curves = ["smile_L"]  # 補正除外に当たるものは数えない（焼かれない）
    assert not V.needs_extreme(doc, 0, (1, 2))
    assert not V.needs_extreme(doc, 0, (0, 0))  # 点が無い


def test_needs_extreme_emotion_follows_neutral_when_differential():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 1.4
    doc.layers.append(m.Layer(name="Joy"))
    doc.layers[1].points[(1, 2)] = m.GridPoint(1, 2, True, m.SourcePose({"bs.smile_R": 0.5}, {}))
    assert V.needs_extreme(doc, 1, (1, 2))  # 自分に 1 超は無いが、Neutral の誇張を引くため要る
    doc.bake = m.Bake(differential=False)
    assert not V.needs_extreme(doc, 1, (1, 2))


def test_extreme_unbaked_orphan_and_stale():
    doc = make_doc()
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 1.5
    h = V.pose_hash(doc.layers[0].points[(1, 2)].pose)
    scene = make_scene(doc)
    # 通常だけ焼いてある → 誇張が未ベイク（変更扱い）
    bake = {MORPH: h}
    assert any(i.code == "point_changed_since_bake" and i.name == EX for i in V.validate(doc, scene, None, bake))
    # 両方ある → 問題なし
    scene.targets = {MORPH, EX}
    ok = V.validate(doc, scene, None, {MORPH: h, EX: h})
    assert not {"point_changed_since_bake", "point_unbaked", "orphan_target", "baked_morph_missing"} & set(codes(ok))
    # _Ex がモデルから消えた
    scene.targets = {MORPH}
    assert any(i.code == "point_changed_since_bake" and i.name == EX for i in V.validate(doc, scene, None, {MORPH: h, EX: h}))
    # 誇張が要らなくなった点の _Ex は孤立
    doc.layers[0].points[(1, 2)].pose.curves["bs.smile_L"] = 1.0
    scene.targets = {MORPH, EX}
    got = [i.name for i in V.validate(doc, scene, None, {MORPH: V.pose_hash(doc.layers[0].points[(1, 2)].pose)}) if i.code == "orphan_target"]
    assert got == [EX]


def test_layer_weights_validation():
    doc = make_doc()
    doc.layers.append(m.Layer(name="Joy"))
    doc.layer_weights = {"Joy": {"source": "distance", "start": 40, "end": 120, "from": 0, "to": 1}}
    assert not [c for c in codes(V.validate(doc, make_scene(doc))) if c.startswith("layer_weight")]
    doc.layer_weights = {"Ghost": {"source": "direct"}, "Joy": {"source": "weird"}}
    got = codes(V.validate(doc, make_scene(doc)))
    assert "layer_weight_unknown_layer" in got and "layer_weight_invalid" in got
    doc.layer_weights = {"Joy": {"source": "distance", "start": -1, "end": 2, "from": 0, "to": 3}}
    assert "layer_weight_invalid" in codes(V.validate(doc, make_scene(doc)))
    doc.layer_weights = {"Joy": {"source": "distance", "start": "a", "end": 2, "from": 0, "to": 1}}
    assert "layer_weight_invalid" in codes(V.validate(doc, make_scene(doc)))
