"""部位別の強さ（bake.partStrength。F5-6）: 掛け方・優先順位・読み書き・検証・ベイク時の設定の指紋（Maya なし）。"""

import copy
import math

import pytest

from tdrive_facial.core import fcpose_io
from tdrive_facial.core import model as m
from tdrive_facial.core import strength as S
from tdrive_facial.core import validate as V


def quat(axis, deg):
    h = math.radians(deg) / 2
    n = math.sqrt(sum(a * a for a in axis))
    s = math.sin(h) / n
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def make_doc(entries=(), exclude_curves=(), exclude_bones=()):
    doc = m.Document()
    doc.asset = "a"
    doc.bake = m.Bake(part_strength=[m.PartStrength(p, s) for p, s in entries])
    doc.exclude.curves = list(exclude_curves)
    doc.exclude.bones = list(exclude_bones)
    return doc


def pose():
    return m.SourcePose(
        curves={"bs.eye_close_L": 1.0, "bs.mouth_open": 0.8, "bs.eye_wide": 1.5},
        bones={
            "eye_L": m.BoneOffset(t=(1.0, 2.0, 0.0), r=quat((0, 1, 0), 40.0), s=(2.0, 1.0, 0.5)),
            "head": m.BoneOffset(t=(1.0, 0.0, 0.0)),
        },
    )


# --- 掛け方 ---


def test_no_entries_returns_same_pose_object():
    doc = make_doc()
    p = pose()
    assert S.scale_pose(doc, p) is p
    assert S.scale_pose(m.Document(), p) is p  # bake が無い文書


def test_curves_scaled_and_source_untouched():
    doc = make_doc([("eye", 0.5)])
    p = pose()
    before = copy.deepcopy(p)
    out = S.scale_pose(doc, p)
    assert out.curves["bs.eye_close_L"] == pytest.approx(0.5)
    assert out.curves["bs.eye_wide"] == pytest.approx(0.75)
    assert out.curves["bs.mouth_open"] == pytest.approx(0.8)  # 当たらない
    assert p.curves == before.curves and p.bones == before.bones  # 元は変えない


def test_bone_translation_rotation_scale():
    doc = make_doc([("eye_L", 0.5)])
    out = S.scale_pose(doc, pose())
    b = out.bones["eye_L"]
    assert b.t == pytest.approx((0.5, 1.0, 0.0))
    # 回転は恒等との球面補間: 40 度 → 20 度（同じ軸）
    want = quat((0, 1, 0), 20.0)
    assert b.r == pytest.approx(want, abs=1e-9)
    # スケールは 1 との線形補間
    assert b.s == pytest.approx((1.5, 1.0, 0.75))
    assert out.bones["head"].t == (1.0, 0.0, 0.0)  # 当たらない


def test_strength_zero_and_one():
    doc = make_doc([("eye_L", 0.0), ("head", 1.0)])
    out = S.scale_pose(doc, pose())
    b = out.bones["eye_L"]
    assert b.t == (0.0, 0.0, 0.0) and b.r == (0.0, 0.0, 0.0, 1.0) and b.s == (1.0, 1.0, 1.0)
    assert out.bones["head"].t == (1.0, 0.0, 0.0)
    assert S.scale_pose(make_doc([("eye", 0.0)]), pose()).curves["bs.eye_close_L"] == 0.0


def test_rotation_takes_short_way_for_negative_w():
    doc = make_doc([("eye_L", 0.5)])
    q = quat((0, 1, 0), 40.0)
    neg = tuple(-v for v in q)  # 同じ回転の反対側の表現
    p = m.SourcePose(bones={"eye_L": m.BoneOffset(r=neg)})
    r = S.scale_pose(doc, p).bones["eye_L"].r
    want = quat((0, 1, 0), 20.0)
    assert r == pytest.approx(want, abs=1e-9)
    # 大きい回転（350 度 = -10 度の反対側）も短い側で扱う
    big = quat((0, 1, 0), 350.0)
    r2 = S.scale_pose(doc, m.SourcePose(bones={"eye_L": m.BoneOffset(r=big)})).bones["eye_L"].r
    assert r2 == pytest.approx(quat((0, 1, 0), -5.0), abs=1e-9)


def test_rotation_is_unit_and_identity_stays():
    doc = make_doc([("e", 0.3)])
    p = m.SourcePose(bones={"e": m.BoneOffset(r=quat((1, 2, 3), 77.0)), "e2": m.BoneOffset()})
    out = S.scale_pose(doc, p)
    assert math.isclose(sum(v * v for v in out.bones["e"].r), 1.0, abs_tol=1e-12)
    assert out.bones["e2"].r == (0.0, 0.0, 0.0, 1.0)


def test_unknown_bone_keys_kept():
    doc = make_doc([("e", 0.5)])
    b = m.BoneOffset(t=(2.0, 0, 0), extra={"x": 1})
    assert S.scale_pose(doc, m.SourcePose(bones={"e": b})).bones["e"].extra == {"x": 1}


# --- 優先順位・除外 ---


def test_first_matching_entry_wins():
    doc = make_doc([("eye_close", 0.2), ("eye", 0.8)])
    out = S.scale_pose(doc, pose())
    assert out.curves["bs.eye_close_L"] == pytest.approx(0.2)  # 上が優先
    assert out.curves["bs.eye_wide"] == pytest.approx(1.2)  # 下の行に当たる
    swapped = make_doc([("eye", 0.8), ("eye_close", 0.2)])
    assert S.scale_pose(swapped, pose()).curves["bs.eye_close_L"] == pytest.approx(0.8)


def test_substring_match_is_case_sensitive_and_empty_ignored():
    doc = make_doc([("EYE", 0.5), ("", 0.1)])
    out = S.scale_pose(doc, pose())
    assert out.curves["bs.eye_close_L"] == 1.0  # 大文字小文字は区別する
    assert S.effective_entries(doc) == [("EYE", 0.5)]


def test_exclusion_wins_over_strength():
    doc = make_doc([("eye", 0.5)], exclude_curves=["eye_close"], exclude_bones=["eye_L"])
    out = S.scale_pose(doc, pose())
    assert out.curves["bs.eye_close_L"] == 1.0  # 除外は別に効く（焼かれない）ので、強さは触らない
    assert out.curves["bs.eye_wide"] == pytest.approx(0.75)
    assert out.bones["eye_L"].t == (1.0, 2.0, 0.0)
    assert S.curve_factor(doc, "bs.eye_close_L") == 1.0
    assert S.curve_factor(doc, "bs.eye_wide") == 0.5


def test_effective_entries_clamp_and_skip_nonfinite():
    doc = make_doc([("a", 1.7), ("b", -0.2), ("c", float("nan")), ("d", float("inf"))])
    assert S.effective_entries(doc) == [("a", 1.0), ("b", 0.0)]


def test_extreme_uses_scaled_weight():
    doc = make_doc([("eye_wide", 0.5)])
    p = pose()
    assert not V.has_extreme(doc, p)  # 1.5 × 0.5 = 0.75
    assert V.has_extreme(make_doc([("eye_wide", 0.9)]), p)  # 1.35
    assert V.has_extreme(make_doc(), p)


# --- 読み書き ---


def test_roundtrip_and_unknown_keys():
    doc = make_doc([("eye", 0.5)])
    doc.bake.part_strength[0].extra = {"note": "x"}
    doc.target = m.Target(mesh="face")
    d = fcpose_io.to_dict(doc)
    assert d["bake"]["partStrength"] == [{"pattern": "eye", "strength": 0.5, "note": "x"}]
    back = fcpose_io.from_dict(d)
    assert [(e.pattern, e.strength, e.extra) for e in back.bake.part_strength] == [("eye", 0.5, {"note": "x"})]
    assert fcpose_io.to_dict(back) == d


def test_absent_means_none_and_not_written():
    doc = make_doc()
    d = fcpose_io.to_dict(doc)
    assert "partStrength" not in d["bake"]
    assert fcpose_io.from_dict(d).bake.part_strength == []
    old = {"format": "FacialCorrection", "version": 1, "bake": {"deltaThreshold": 0.002}}
    assert fcpose_io.from_dict(old).bake.part_strength == []


def test_broken_entries_skipped():
    d = fcpose_io.to_dict(make_doc())
    d["bake"]["partStrength"] = [{"pattern": "a", "strength": 0.3}, {"strength": 1}, "x", {"pattern": 3}]
    assert [(e.pattern, e.strength) for e in fcpose_io.from_dict(d).bake.part_strength] == [("a", 0.3)]


def test_schema_accepts_part_strength():
    import json
    from pathlib import Path

    from test_fcpose_io import mini_validate

    schema = json.loads((Path(__file__).parents[2] / "schema" / "fcpose.schema.json").read_text(encoding="utf-8"))
    doc = make_doc([("eye", 0.5)])
    doc.target = m.Target(mesh="f", lod_meshes=[m.LodMesh("f_LOD1", 1)])
    d = fcpose_io.to_dict(doc)
    assert not mini_validate(d, schema, schema)
    d["bake"]["partStrength"][0]["strength"] = 1.5
    assert mini_validate(d, schema, schema)  # 範囲外は不適合


# --- 検証 ---


def codes(doc):
    return [i.code for i in V.validate(doc, V.SceneInfo())]


def test_validate_codes():
    assert "part_strength_pattern_empty" in codes(make_doc([("  ", 0.5)]))
    dup = codes(make_doc([("eye", 0.5), ("eye", 0.2)]))
    assert dup.count("part_strength_pattern_duplicate") == 1
    assert "part_strength_out_of_range" in codes(make_doc([("eye", 1.5)]))
    assert "part_strength_out_of_range" in codes(make_doc([("eye", -0.1)]))
    assert "part_strength_not_finite" in codes(make_doc([("eye", float("nan"))]))
    ok = codes(make_doc([("eye", 0.5), ("mouth", 0.0), ("brow", 1.0)]))
    assert not [c for c in ok if c.startswith("part_strength")]


# --- ベイク時の設定の指紋（古い記録との互換）---


def test_signature_legacy_compatible_when_no_part_strength():
    doc = make_doc(exclude_curves=["smile"], exclude_bones=["eye_"])
    legacy = V.exclude_signature(doc)
    assert len(legacy) == 12 and "+" not in legacy  # 従来の記録と同じ形・同じ値
    # 部位別の強さが空 / 指定はあっても効かない（空パターン）のときも同じ
    assert V.exclude_signature(make_doc([("", 0.5)], ["smile"], ["eye_"])) == legacy
    assert V.bake_setting_changed(legacy, V.exclude_signature(doc)) == ""


def test_signature_hash_value_is_stable_for_legacy_scenes():
    # 従来の式（sha1 の先頭 12 文字）で作った値と同じであること（この値が変わると、既存シーンが一斉に変更ありになる）
    import hashlib
    import json

    doc = make_doc(exclude_curves=["b", "a"], exclude_bones=[])
    norm = json.dumps({"curves": ["a", "b"], "bones": []}, sort_keys=True, ensure_ascii=False)
    assert V.exclude_signature(doc) == hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def test_signature_with_part_strength_and_reasons():
    base = make_doc(exclude_curves=["smile"])
    sig0 = V.exclude_signature(base)
    ps = make_doc([("eye", 0.5)], ["smile"])
    sig1 = V.exclude_signature(ps)
    assert sig1 != sig0 and sig1.startswith(sig0 + "+")
    assert V.bake_setting_changed(sig0, sig1) == V.SETTING_PART  # 従来の記録 → 部位別の強さを足した
    assert V.bake_setting_changed(sig1, sig0) == V.SETTING_PART  # 外した
    assert V.bake_setting_changed(sig1, V.exclude_signature(make_doc([("eye", 0.6)], ["smile"]))) == V.SETTING_PART
    assert V.bake_setting_changed(sig1, V.exclude_signature(make_doc([("eye", 0.5)], ["smile", "x"]))) == V.SETTING_EXCLUDE
    assert V.bake_setting_changed(sig0, V.exclude_signature(make_doc([("eye", 0.5)], ["smile", "x"]))) == V.SETTING_BOTH
    assert V.bake_setting_changed(None, sig1) == ""  # 記録なし = 不明
    # 順番も指紋に入る（上が優先）
    assert V.exclude_signature(make_doc([("a", 0.1), ("b", 0.2)])) != V.exclude_signature(make_doc([("b", 0.2), ("a", 0.1)]))
    # 範囲外の強さは 0〜1 に収めた値で比べる（1.5 と 1.0 は同じ焼き結果）
    assert V.exclude_signature(make_doc([("a", 1.5)])) == V.exclude_signature(make_doc([("a", 1.0)]))


def _stale_doc(entries):
    doc = make_doc(entries)
    doc.layers[0].points[(0, 0)] = m.GridPoint(0, 0, True, m.SourcePose(curves={"bs.eye": 1.0}))
    return doc


def test_stale_messages_by_cause():
    from tdrive_facial.core import naming

    morph = naming.morph_name("a", "Neutral", 0, 0)
    base = _stale_doc([])
    h = V.pose_hash(base.layers[0].points[(0, 0)].pose)

    def msgs(doc, rec):
        return [i.message for i in V.validate(doc, V.SceneInfo(), bake_state={morph: h}, bake_exclude={morph: rec}) if i.code == "point_changed_since_bake"]

    legacy = V.exclude_signature(base)
    assert msgs(base, legacy) == []  # 従来の記録のまま・部位別の強さなし = 変更なし
    only_part = msgs(_stale_doc([("eye", 0.5)]), legacy)
    assert len(only_part) == 1 and "部位別の強さを変えたあと、焼き直していません" in only_part[0] and "除外" not in only_part[0]
    excl = base
    excl.exclude.curves = ["x"]
    only_excl = msgs(excl, legacy)
    assert len(only_excl) == 1 and "補正から除外するものを変えたあと、焼き直していません" in only_excl[0]
    both = _stale_doc([("eye", 0.5)])
    both.exclude.curves = ["x"]
    assert "補正から除外するもの・部位別の強さを変えたあと、焼き直していません" in msgs(both, legacy)[0]
    # 焼いたときと同じ指紋なら変更なし
    assert msgs(both, V.exclude_signature(both)) == []


# --- 一覧の編集（session が Undo の印を付けて呼ぶ）---


def test_edit_functions():
    doc = m.Document()
    assert S.add_entry(doc, " eye ", 0.5) is None and doc.bake is not None  # bake が無くても作る
    assert S.add_entry(doc, "mouth", 0.25) is None
    assert S.add_entry(doc, "eye", 0.1)[0] == "exists"
    assert S.add_entry(doc, " ", 0.1)[0] == "empty"
    assert S.add_entry(doc, "x", 1.5)[0] == "strength_range"
    assert S.add_entry(doc, "x", float("nan"))[0] == "strength_invalid"
    assert S.add_entry(doc, "x", True)[0] == "strength_invalid"
    assert [e.pattern for e in doc.bake.part_strength] == ["eye", "mouth"]  # 失敗では変わらない
    assert S.move_entry(doc, 1, -1) is None and [e.pattern for e in doc.bake.part_strength] == ["mouth", "eye"]
    assert S.move_entry(doc, 0, -1) is None and [e.pattern for e in doc.bake.part_strength] == ["mouth", "eye"]  # 端は動かない
    assert S.set_entry_strength(doc, 0, 0.9) is None and doc.bake.part_strength[0].strength == 0.9
    assert S.set_entry_strength(doc, 5, 0.9)[0] == "index"
    assert S.remove_entry(doc, 0) is None and [e.pattern for e in doc.bake.part_strength] == ["eye"]
    assert S.remove_entry(doc, 3)[0] == "index"
