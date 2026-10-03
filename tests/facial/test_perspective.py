"""パース補正（R-34、F5-4）の core: データ・読み書き・重み・検証・Presenter・fctrack。"""

import copy
import json
import math
import warnings

import pytest

from tdrive_facial.core import evaluate as ev
from tdrive_facial.core import fcpose_io
from tdrive_facial.core import fctrack as ft
from tdrive_facial.core import model as m
from tdrive_facial.core import naming
from tdrive_facial.core import presenters as P
from tdrive_facial.core import validate as V

CURVES = {"bs.smile_L", "bs.jaw_open", "bs.smile_R"}


def key(value, **curves):
    return m.PerspectiveKey(value=value, curves=dict(curves))


def make_doc(keys=None, axis="distance", enabled=True, strength=1.0) -> m.Document:
    doc = m.Document()
    doc.asset = "a"
    doc.perspective = m.Perspective(
        enabled=enabled,
        axis=axis,
        strength=strength,
        keys=keys if keys is not None else [key(30.0, **{"bs.smile_L": 0.5}), key(80.0)],
    )
    return doc


def scene(**kw) -> V.SceneInfo:
    d = dict(curves=set(CURVES), bones={"head"}, targets=set())
    d.update(kw)
    return V.SceneInfo(**d)


def codes(issues):
    return [i.code for i in issues]


# --- 読み書き ---


def test_round_trip_keeps_keys_and_unknown_keys():
    raw = {
        "enabled": True,
        "axis": "fov",
        "strength": 0.5,
        "future": {"x": 1},
        "keys": [
            {"value": 30, "curves": {"bs.smile_L": 0.5}, "bones": {"head": {"t": [0, 1, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1]}}, "note": "n"},
            {"value": 80.5, "curves": {}, "bones": {}},
        ],
    }
    d = fcpose_io.to_dict(fcpose_io.from_dict({"format": "FacialCorrection", "version": 1, "perspective": copy.deepcopy(raw)}))
    assert d["perspective"] == raw
    p = fcpose_io.from_dict({"format": "FacialCorrection", "version": 1, "perspective": raw}).perspective
    assert p.axis == "fov" and p.strength == 0.5 and p.keys[0].value == 30.0
    assert p.keys[0].curves == {"bs.smile_L": 0.5} and "head" in p.keys[0].bones and p.keys[1].is_empty()


def test_defaults_are_not_written_and_legacy_files_read():
    for raw in ({"enabled": False, "keys": []}, {"enabled": False}, {}):
        doc = fcpose_io.from_dict({"format": "FacialCorrection", "version": 1, "perspective": raw})
        assert doc.perspective.axis == "distance" and doc.perspective.strength == 1.0 and doc.perspective.keys == []
        out = fcpose_io.to_dict(doc)["perspective"]
        assert "axis" not in out and "strength" not in out and out["keys"] == []


def test_non_object_or_valueless_keys_are_dropped_with_warning():
    raw = {"enabled": True, "keys": [5, {"value": "x"}, {"value": 40, "curves": {"a": 1}}, None]}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        doc = fcpose_io.from_dict({"format": "FacialCorrection", "version": 1, "perspective": raw})
    assert [k.value for k in doc.perspective.keys] == [40.0]
    assert len(w) == 3


# --- 重み ---


def test_weights_basic_and_edge_cases():
    assert ev.perspective_weights([], 1.0) == []
    assert ev.perspective_weights([30.0], 5.0) == [1.0]
    assert ev.perspective_weights([30.0], math.nan) == [0.0]
    assert ev.perspective_weights([30.0, 80.0], math.nan) == [0.0, 0.0]
    assert ev.perspective_weights([80.0, 30.0, 50.0], 40.0) == pytest.approx([0.0, 0.5, 0.5])
    assert ev.perspective_weights([30.0, 80.0], -math.inf) == [1.0, 0.0]
    assert ev.perspective_weights([30.0, 80.0], math.inf) == [0.0, 1.0]
    assert ev.perspective_weights([30.0, math.nan, 80.0], 55.0) == pytest.approx([0.5, 0.0, 0.5])


def test_duplicate_values_lower_index_wins():
    assert ev.perspective_weights([50.0, 30.0, 50.0], 100.0) == [1.0, 0.0, 0.0]
    assert ev.perspective_weights([50.0, 50.0], 50.0) == [1.0, 0.0]


def test_weights_sum_to_one_inside_valid_keys():
    vals = [80.0, 30.0, 50.0, 120.0]
    for x in (0.0, 30.0, 33.0, 50.0, 70.0, 100.0, 120.0, 500.0):
        assert sum(ev.perspective_weights(vals, x)) == pytest.approx(1.0)


def test_morph_weights_names_strength_alpha_and_empty_keys():
    doc = make_doc([key(30.0, **{"bs.smile_L": 1.0}), key(50.0), key(80.0, **{"bs.jaw_open": 1.0})])
    got = ev.perspective_morph_weights(doc, 40.0)
    assert got == pytest.approx({"FC_a_Persp_K0": 0.5})  # K1 は空のキー = シェイプ名なし
    got = ev.perspective_morph_weights(doc, 65.0, strength=0.5, alpha=0.5)
    assert got == pytest.approx({"FC_a_Persp_K2": 0.125})
    assert ev.perspective_morph_weights(doc, 30.0, strength=0.0) == {}
    assert ev.perspective_morph_weights(doc, 30.0, strength=9) == pytest.approx({"FC_a_Persp_K0": 1.0})  # 強さは 0〜1 に丸める
    doc.perspective.strength = 0.4
    assert ev.perspective_morph_weights(doc, 30.0) == pytest.approx({"FC_a_Persp_K0": 0.4})


def test_morph_weights_axis_and_disabled():
    doc = make_doc([key(30.0, **{"bs.smile_L": 1.0}), key(60.0, **{"bs.jaw_open": 1.0})], axis="fov")
    assert ev.perspective_morph_weights(doc, 999.0) == {}  # 画角が分からない = NaN = 0
    assert ev.perspective_morph_weights(doc, 999.0, fov_deg=math.nan) == {}
    assert ev.perspective_morph_weights(doc, 999.0, fov_deg=45.0) == pytest.approx({"FC_a_Persp_K0": 0.5, "FC_a_Persp_K1": 0.5})
    doc.perspective.enabled = False
    assert ev.perspective_morph_weights(doc, 1.0, fov_deg=45.0) == {}
    doc.perspective = None
    assert ev.perspective_morph_weights(doc, 1.0) == {}
    assert ev.perspective_morph_weights(make_doc([]), 1.0) == {}


# --- 検証 ---


def test_structure_issues():
    doc = make_doc([key(30.0, **{"bs.smile_L": 1}), key(30.0, **{"bs.smile_L": 1}), key(-5.0), key(math.nan)])
    doc.perspective.axis = "x"
    doc.perspective.strength = 2.0
    got = codes(V.validate(doc, scene()))
    for c in ("perspective_axis_invalid", "perspective_strength_invalid", "perspective_key_duplicate", "perspective_key_value_invalid"):
        assert c in got, c
    doc = make_doc([key(v, **{"bs.smile_L": 1}) for v in range(10, 100, 10)])  # 9 個
    assert "perspective_key_count_exceeded" in codes(V.validate(doc, scene()))
    doc = make_doc([key(0.0), key(180.0), key(90.0)], axis="fov")
    bad = [i.key for i in V.validate(doc, scene()) if i.code == "perspective_key_value_invalid"]
    assert bad == [0, 1]
    assert [i.code for i in V.validate(make_doc(), scene()) if i.code.startswith("perspective")] == []


def test_missing_and_excluded_curves_in_keys():
    doc = make_doc([key(30.0, **{"bs.smile_l": 1.0, "bs.nothing": 0.5}), key(80.0)])
    got = V.validate(doc, scene())
    assert {i.name: i.code for i in got if i.code.startswith("curve_")} == {"bs.smile_l": "curve_case_mismatch", "bs.nothing": "curve_missing"}
    doc = make_doc([key(30.0, **{"bs.smile_L": 1.0})])
    doc.exclude.curves = ["bs.smile_L"]
    ex = [i for i in V.validate(doc, scene()) if i.code == "excluded_in_pose"]
    assert ex and ex[0].key == 0
    doc = make_doc([key(30.0, **{"bs.smile_L": 5.0})])
    assert any(i.code == "limit_exceeded" and i.key == 0 for i in V.validate(doc, scene()))


def test_bake_states_and_removal_orphans():
    doc = make_doc([key(30.0, **{"bs.smile_L": 0.5}), key(50.0), key(80.0, **{"bs.jaw_open": 0.7})])
    h0, h2 = V.perspective_key_hash(doc.perspective.keys[0]), V.perspective_key_hash(doc.perspective.keys[2])
    # 未ベイク（空のキーは対象外）
    got = V.validate(doc, scene(), bake_state={})
    assert [(i.code, i.key) for i in got if i.code.startswith("perspective_key")] == [("perspective_key_unbaked", 0), ("perspective_key_unbaked", 2)]
    # ベイク済み
    bs = {"FC_a_Persp_K0": h0, "FC_a_Persp_K2": h2}
    assert not [i for i in V.validate(doc, scene(targets=set(bs)), bake_state=bs) if i.code.startswith("perspective")]
    # value を変えても焼き直しは要らない（ポーズだけがハッシュ）
    doc.perspective.keys[0].value = 31.0
    assert not [i for i in V.validate(doc, scene(targets=set(bs)), bake_state=bs) if i.code.startswith("perspective")]
    # ポーズを変えた
    doc.perspective.keys[0].curves["bs.smile_L"] = 0.6
    ch = [i for i in V.validate(doc, scene(targets=set(bs)), bake_state=bs) if i.code == "perspective_key_changed"]
    assert [i.key for i in ch] == [0]
    # シェイプが消えた
    doc.perspective.keys[0].curves["bs.smile_L"] = 0.5
    assert "baked_morph_missing" in codes(V.validate(doc, scene(targets={"FC_a_Persp_K2"}), bake_state=bs))
    # 空のキーを削除 → 詰まった後ろのキー（K2 → K1）は未ベイク / いちばん後ろの K2 は孤立
    del doc.perspective.keys[1]
    got = V.validate(doc, scene(targets=set(bs)), bake_state=bs)
    assert [i.key for i in got if i.code == "perspective_key_unbaked"] == [1]
    assert [i.name for i in got if i.code == "orphan_target"] == ["FC_a_Persp_K2"]
    # 空のキーのシェイプは孤立
    doc2 = make_doc([key(30.0, **{"bs.smile_L": 0.5}), key(80.0)])
    got = V.validate(doc2, scene(targets={"FC_a_Persp_K0", "FC_a_Persp_K1"}), bake_state={"FC_a_Persp_K0": V.perspective_key_hash(doc2.perspective.keys[0])})
    assert [i.name for i in got if i.code == "orphan_target"] == ["FC_a_Persp_K1"]


def test_rename_and_remove_missing_cover_keys():
    doc = make_doc([key(30.0, **{"bs.old": 0.5}), key(80.0)])
    assert V.rename_references(doc, {"bs.old": "bs.new"}, "curve") == 1
    assert "bs.new" in doc.perspective.keys[0].curves
    assert V.remove_missing_references(doc, scene()) == 1
    assert doc.perspective.keys[0].curves == {}


# --- Presenter ---


def make_ps(doc=None, bake=None, sc=None):
    return P.PresenterSet(doc or make_doc(), bake_state=bake, scene=sc).perspective


def test_presenter_settings_and_keys():
    doc = m.Document()
    doc.asset = "a"
    pp = make_ps(doc)
    v = pp.view()
    assert not v.present and v.count == 0 and v.suggested_value == 30.0 and v.can_add
    assert pp.set_enabled(True).ok and doc.perspective.enabled
    assert pp.set_strength(1.5).code == "strength" and doc.perspective.strength == 1.0
    assert pp.set_strength(0.4).ok and doc.perspective.strength == 0.4
    assert pp.set_axis("zzz").code == "axis"
    assert pp.add_key(0).code == "value" and pp.add_key(math.nan).code == "value" and pp.add_key("a").code == "value"
    assert pp.add_key(30).index == 0
    assert pp.add_key(30).code == "duplicate"
    assert pp.add_key(80).index == 1
    assert pp.view().suggested_value == 130.0
    assert pp.set_value(1, 30).code == "duplicate" and doc.perspective.keys[1].value == 80.0
    assert pp.set_value(1, 90).ok and doc.perspective.keys[1].value == 90.0
    assert pp.set_value(1, 90).code == "unchanged"
    assert pp.set_value(5, 1).code == "range"
    assert pp.set_axis("fov").ok
    assert pp.add_key(180).code == "value" and pp.add_key(45).ok
    for i in range(5):
        assert pp.add_key(10 + i).ok
    assert pp.add_key(100 - 1).code == "limit" and not pp.view().can_add


def test_presenter_pose_edit_and_rows():
    sc = scene(targets={"FC_a_Persp_K0"})
    doc = make_doc([key(30.0), key(80.0)])
    pp = make_ps(doc, bake={}, sc=sc)
    rows = pp.view().keys
    assert [r.has_pose for r in rows] == [False, False] and rows[0].bake == P.BAKE_NONE
    assert pp.set_key_pose(0, m.SourcePose(curves={"bs.smile_L": 0.5, "bs.jaw_open": 0.0})).ok
    assert doc.perspective.keys[0].curves == {"bs.smile_L": 0.5}  # ほぼ 0 は捨てる
    assert pp.set_key_pose(0, pp.key_pose(0)).code == "unchanged"
    assert pp.set_key_pose(9, m.SourcePose()).code == "range"
    r = pp.view().keys[0]
    assert r.has_pose and r.shape == "FC_a_Persp_K0" and r.bake == P.BAKE_UNBAKED and "未ベイク" in r.bake_text
    pp.ctx.bake_state["FC_a_Persp_K0"] = V.perspective_key_hash(doc.perspective.keys[0])
    assert pp.bake_status(0) == P.BAKE_BAKED
    pose = pp.key_pose(0)
    pose.curves["bs.smile_L"] = 0.9
    assert doc.perspective.keys[0].curves["bs.smile_L"] == 0.5  # 複製を返す
    assert pp.set_key_pose(0, pose).ok and pp.bake_status(0) == P.BAKE_CHANGED
    assert pp.clear_key_pose(0).ok and pp.view().keys[0].shape == ""
    assert "後ろ" in P.PERSPECTIVE_REMOVE_NOTE and pp.view().remove_note


def test_presenter_remove_is_real_removal_and_validate_reports_it():
    doc = make_doc([key(30.0, **{"bs.smile_L": 0.5}), key(50.0, **{"bs.jaw_open": 0.5}), key(80.0, **{"bs.smile_R": 0.5})])
    bs = {f"FC_a_Persp_K{i}": V.perspective_key_hash(k) for i, k in enumerate(doc.perspective.keys)}
    sc = scene(targets=set(bs))
    pp = make_ps(doc, bake=bs, sc=sc)
    res = pp.remove_key(0)
    assert res.ok and [k.value for k in doc.perspective.keys] == [50.0, 80.0]
    assert res.stale_morphs == ["FC_a_Persp_K2"] and "再ベイク" in res.message
    got = V.validate(doc, sc, bake_state=bs)
    assert sorted(i.key for i in got if i.code == "perspective_key_changed") == [0, 1]
    assert [i.name for i in got if i.code == "orphan_target"] == ["FC_a_Persp_K2"]
    assert pp.remove_key(7).code == "range"
    # 最後のキーを消すだけなら、後ろの番号は詰まらない
    res = pp.remove_key(1)
    assert res.ok and "詰まった" not in res.message


def test_presenter_events_and_single_edit_is_one_change():
    doc = make_doc()
    pp = make_ps(doc)
    events = []
    pp.subscribe(events.append)
    pp.set_strength(0.3)
    assert "perspective" in events


# --- fctrack ---


def test_fctrack_perspective_curve():
    t = ft.FacialTrack(shot="S", model="m", frame_rate=30, range=(0, 30), curves={"perspective": [(0.0, 0.0), (1.0, 1.0)]})
    assert ft.validate(t) == []
    assert "perspective" in ft.FIXED_CURVES
    again = ft.loads(ft.dumps(t))
    assert again.curves["perspective"] == [(0.0, 0.0), (1.0, 1.0)]
    assert json.loads(ft.dumps(t))["curves"]["perspective"] == [[0.0, 0.0], [1.0, 1.0]]
