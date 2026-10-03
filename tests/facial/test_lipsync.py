"""リップシンクの対応表（R-18、F5-8）の core: 読み書き・検証・Presenter・編集の対象・プロファイル。
計算そのものは共通のテストデータ（conformance/lipsync.json）で確かめる。"""

import json
import math
import warnings

import pytest

from tdrive_facial.core import evaluate as ev
from tdrive_facial.core import fcpose_io
from tdrive_facial.core import model as m
from tdrive_facial.core import presenters as P
from tdrive_facial.core import profile as prof
from tdrive_facial.core import validate as V


def make_lip() -> m.LipSync:
    return m.LipSync(
        phonemes=["A", "I"],
        entries=[
            m.LipSyncEntry("A", "", {"bs.a": 1.0}),
            m.LipSyncEntry("A", "Joy", {"bs.a": 0.8, "bs.smile": 0.3}),
            m.LipSyncEntry("I", "", {"bs.i": 1.0}),
        ],
    )


def make_set(lip=True) -> P.PresenterSet:
    doc = m.Document()
    doc.asset = "a"
    doc.layers.append(m.Layer(name="Joy", emotion_curve="Joy"))
    doc.layers.append(m.Layer(name="Sad", emotion_curve="Sad"))
    if lip:
        doc.lip_sync = make_lip()
    return P.PresenterSet(doc)


def codes(doc, scene=None, profile=None):
    return [i.code for i in V.validate(doc, scene or V.SceneInfo(), profile)]


# --- 読み書き ---


def test_absent_lipsync_is_none_and_not_written():
    d = fcpose_io.from_dict({"format": "FacialCorrection", "version": 1})
    assert d.lip_sync is None
    assert "lipSync" not in fcpose_io.to_dict(d)


def test_round_trip_keeps_unknown_keys_and_uses_from_to():
    src = {
        "format": "FacialCorrection",
        "version": 1,
        "lipSync": {
            "enabled": False,
            "strength": 0.5,
            "phonemes": ["A", "I"],
            "entries": [{"phoneme": "A", "emotion": "Joy", "curves": {"x": 0.5}, "memo": 1}],
            "volume": {"min": 0.1, "max": 0.9, "from": 0.2, "to": 1.5, "curve": "lin"},
            "follow": 12,
            "future": {"k": 1},
        },
    }
    d = fcpose_io.from_dict(src)
    l = d.lip_sync
    assert (l.enabled, l.strength, l.follow) == (False, 0.5, 12.0)
    assert l.volume.from_ == 0.2 and l.volume.to == 1.5 and l.volume.extra == {"curve": "lin"}
    assert l.entries[0].extra == {"memo": 1} and l.extra == {"future": {"k": 1}}
    out = fcpose_io.to_dict(d)["lipSync"]
    assert out["volume"]["from"] == 0.2 and out["volume"]["to"] == 1.5 and out["volume"]["curve"] == "lin"
    assert out["entries"][0] == {"phoneme": "A", "emotion": "Joy", "curves": {"x": 0.5}, "memo": 1}
    assert out["future"] == {"k": 1}
    again = fcpose_io.from_dict(json.loads(fcpose_io.dumps(d)))
    assert fcpose_io.to_dict(again) == fcpose_io.to_dict(d)


def test_defaults_when_keys_missing():
    d = fcpose_io.from_dict({"format": "FacialCorrection", "lipSync": {}})
    l = d.lip_sync
    assert l.enabled is True and l.strength == 1.0 and l.phonemes == [] and l.entries == []
    assert (l.volume.min, l.volume.max, l.volume.from_, l.volume.to) == (0.0, 1.0, 0.5, 1.0) and l.follow == 20.0


def test_bad_entries_are_dropped_with_warning():
    src = {
        "format": "FacialCorrection",
        "lipSync": {"entries": ["x", {"emotion": "Joy"}, {"phoneme": "A", "curves": {"a": "no", "b": 1}}]},
    }
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        d = fcpose_io.from_dict(src)
    assert len(d.lip_sync.entries) == 1 and d.lip_sync.entries[0].curves == {"b": 1.0}
    assert len(w) >= 3


def test_schema_has_lipsync():
    from pathlib import Path

    schema = json.loads((Path(__file__).parents[2] / "schema" / "fcpose.schema.json").read_text(encoding="utf-8"))
    assert '"lipSync"' in json.dumps(schema)


# --- 計算（共通のテストデータの補足）---


def test_table_curves_exclude_entries_of_unknown_phonemes():
    l = make_lip()
    l.entries.append(m.LipSyncEntry("Z", "", {"bs.z": 1.0}))
    assert ev.lipsync_table_curves(l) == ["bs.a", "bs.smile", "bs.i"]


def test_activity_with_names_ignores_unknown():
    assert ev.lipsync_activity({"A": 0.5, "X": 1.0}) == 1.0
    assert ev.lipsync_activity({"A": 0.5, "X": 1.0}, ["A"]) == 0.5


def test_nan_weight_and_nan_volume_are_safe():
    l = make_lip()
    out = ev.lipsync_output(l, {"A": math.nan, "I": 1.0}, math.nan)
    assert out["bs.a"] == 0.0 and out["bs.i"] == pytest.approx(1.0)


# --- 検証 ---


def test_valid_table_has_no_lipsync_issues():
    assert [c for c in codes(make_set().ctx.doc) if c.startswith("lipsync")] == []


def test_validate_codes():
    ps = make_set()
    doc = ps.ctx.doc
    l = doc.lip_sync
    l.entries.append(m.LipSyncEntry("A", "", {"bs.a": 0.1}))  # 重複
    l.entries.append(m.LipSyncEntry("Z", "", {}))  # 音素が無い
    l.entries.append(m.LipSyncEntry("I", "Fear", {}))  # 感情が無い
    l.entries.append(m.LipSyncEntry("I", "Neutral", {}))  # Neutral は基本
    l.phonemes += ["A", " "]
    l.strength = 2.0
    l.volume.to = 3.0
    l.volume.min = -1.0
    l.follow = math.nan
    c = codes(doc)
    for want in (
        "lipsync_entry_duplicate",
        "lipsync_entry_phoneme_unknown",
        "lipsync_entry_emotion_unknown",
        "lipsync_phoneme_duplicate",
        "lipsync_phoneme_empty",
        "lipsync_strength_invalid",
        "lipsync_volume_invalid",
        "lipsync_follow_invalid",
    ):
        assert want in c, want
    msgs = [i.message for i in V.validate(doc, V.SceneInfo()) if i.code == "lipsync_entry_emotion_unknown"]
    assert len(msgs) == 2 and any("基本" in x for x in msgs)


def test_validate_phoneme_count_and_limit_and_missing_shape():
    ps = make_set()
    doc = ps.ctx.doc
    doc.lip_sync.entries[0].curves["bs.a"] = 1.5
    scene = V.SceneInfo(curves=["bs.a", "bs.i"])
    issues = V.validate(doc, scene)
    lim = [i for i in issues if i.code == "limit_exceeded"]
    assert lim and lim[0].lip == ("A", "")
    miss = [i for i in issues if i.code == "curve_missing"]
    assert [i.name for i in miss] == ["bs.smile"]
    doc.lip_sync.phonemes = [f"P{i}" for i in range(m.MAX_LIPSYNC_PHONEMES + 1)]
    assert "lipsync_phoneme_count_exceeded" in codes(doc)


def test_validate_case_mismatch_for_lip_shape():
    doc = make_set().ctx.doc
    issues = V.validate(doc, V.SceneInfo(curves=["bs.A", "bs.smile", "bs.i"]))
    assert any(i.code == "curve_case_mismatch" and i.name == "bs.a" and i.suggestion == "bs.A" for i in issues)


def test_rename_and_remove_references_cover_lip_entries():
    doc = make_set().ctx.doc
    n = V.rename_references(doc, {"bs.a": "bs.aa"}, "curve")
    assert n == 2 and set(doc.lip_sync.find_entry("A", "Joy").curves) == {"bs.aa", "bs.smile"}
    removed = V.remove_missing_references(doc, V.SceneInfo(curves=["bs.aa", "bs.i"]))
    assert removed == 1 and "bs.smile" not in doc.lip_sync.find_entry("A", "Joy").curves


# --- 設定 ---


def test_settings_validation_and_undo_friendly_failures():
    ps = make_set(lip=False)
    lp = ps.lipsync
    assert lp.set_strength(1.5).code == "strength" and ps.ctx.doc.lip_sync is None
    assert lp.set_strength(0.5).ok and ps.ctx.doc.lip_sync.strength == 0.5
    assert lp.set_follow(-1).code == "follow" and lp.set_follow(0).ok
    assert lp.set_volume(min=-0.1).code == "volume" and lp.set_volume(to=2.5).code == "volume"
    assert lp.set_volume(math.nan).code == "volume"
    assert lp.set_volume(min=0.2, max=0.2, from_=0.1, to=2.0).ok
    v = ps.ctx.doc.lip_sync.volume
    assert (v.min, v.max, v.from_, v.to) == (0.2, 0.2, 0.1, 2.0)
    assert lp.set_enabled(False).ok and ps.ctx.doc.lip_sync.enabled is False


# --- 音素 ---


def test_phoneme_add_rename_remove_move():
    ps = make_set()
    lp = ps.lipsync
    doc = ps.ctx.doc
    assert lp.add_phoneme("  ").code == "empty" and lp.add_phoneme("A").code == "duplicate"
    assert lp.add_phoneme("U").ok and doc.lip_sync.phonemes == ["A", "I", "U"]
    assert lp.rename_phoneme("A", "I").code == "duplicate" and lp.rename_phoneme("Q", "R").code == "phoneme"
    assert lp.rename_phoneme("A", "Aa").ok
    assert {e.phoneme for e in doc.lip_sync.entries} == {"Aa", "I"}
    assert lp.move_phoneme("U", 0).ok and doc.lip_sync.phonemes == ["U", "Aa", "I"]
    assert lp.move_phoneme("U", 99).ok and doc.lip_sync.phonemes == ["Aa", "I", "U"]
    assert lp.move_phoneme("U", 2).code == "unchanged"
    r = lp.remove_phoneme("Aa")
    assert r.ok and "行 2 個" in r.message and doc.lip_sync.phonemes == ["I", "U"]
    assert [e.phoneme for e in doc.lip_sync.entries] == ["I"]


def test_phoneme_limit():
    ps = make_set(lip=False)
    for i in range(m.MAX_LIPSYNC_PHONEMES):
        assert ps.lipsync.add_phoneme(f"P{i}").ok
    assert ps.lipsync.add_phoneme("X").code == "limit" and not ps.lipsync.can_add


# --- 表 ---


def test_view_matrix():
    ps = make_set()
    v = ps.lipsync.view()
    assert v.present and v.phonemes == ["A", "I"]
    assert [c.name for c in v.columns] == ["", "Joy", "Sad"] and v.columns[0].label == "基本"
    a = v.rows[0]
    assert [c.has_entry for c in a.cells] == [True, True, False]
    assert a.cells[1].curves == 2 and all(c.valid for c in a.cells)
    assert v.rows[1].cells[0].has_entry and not v.rows[1].cells[1].has_entry
    assert v.orphan_entries == 0 and "音素 2" in v.summary
    ps.ctx.doc.lip_sync.entries.append(m.LipSyncEntry("A", "", {"x": 1}))
    assert not ps.lipsync.view().rows[0].cells[0].valid  # 重複


def test_view_without_data():
    v = make_set(lip=False).lipsync.view()
    assert not v.present and v.rows == [] and v.columns[0].is_base and not v.enabled


# --- マスのポーズ ---


def test_cell_pose_set_clear_and_curves_only():
    ps = make_set()
    lp = ps.lipsync
    pose = m.SourcePose(curves={"bs.o": 0.6, "bs.zero": 0.0}, bones={"jaw": m.BoneOffset(t=(1, 0, 0))})
    assert lp.set_cell_pose("I", "Joy", pose).ok
    e = ps.ctx.doc.lip_sync.find_entry("I", "Joy")
    assert e.curves == {"bs.o": 0.6}
    assert lp.set_cell_pose("I", "Joy", pose).code == "unchanged"
    assert lp.cell_pose("I", "Joy").curves == {"bs.o": 0.6} and lp.cell_pose("I", "Sad").curves == {}
    assert lp.set_cell_pose("I", "Joy", m.SourcePose()).ok and ps.ctx.doc.lip_sync.find_entry("I", "Joy") is None
    assert lp.set_cell_pose("I", "Joy", m.SourcePose(), keep_empty=True).ok
    assert ps.ctx.doc.lip_sync.find_entry("I", "Joy").curves == {}
    assert lp.clear_cell("I", "Joy").ok and lp.clear_cell("I", "Joy").code == "unchanged"
    assert lp.set_cell_pose("Q", "", pose).code == "phoneme" and lp.set_cell_pose("I", "Nope", pose).code == "emotion"


# --- 編集の対象 ---


def test_select_lip_cell_clears_other_targets_and_loads_pose():
    ps = make_set()
    ps.grid.select(1, 1)
    r = ps.lipsync.select_lip_cell("A", "Joy")
    assert r.status == P.SELECT_SELECTED and r.kind == "lip" and (r.phoneme, r.emotion) == ("A", "Joy")
    assert ps.ctx.selection is None and ps.ctx.key_target is None and ps.ctx.selected_lip() == ("A", "Joy")
    assert ps.pose.curves == {"bs.a": 0.8, "bs.smile": 0.3} and not ps.pose.dirty and ps.pose.has_point
    view = ps.pose.view()
    assert view.lip == ("A", "Joy") and view.selection is None and view.key_index is None and view.can_save
    assert ps.lipsync.view().selected == ("A", "Joy") and ps.lipsync.view().rows[0].cells[1].selected
    ps.grid.select(0, 0)
    assert ps.ctx.lip_target is None and ps.ctx.selection == (0, 0)


def test_select_invalid_and_same():
    ps = make_set()
    assert ps.lipsync.select_lip_cell("Z", "").status == P.SELECT_INVALID
    assert ps.lipsync.select_lip_cell("A", "Neutral").status == P.SELECT_INVALID
    ps.lipsync.select_lip_cell("A", "")
    ps.pose.set_curve("bs.x", 0.3)
    r = ps.lipsync.select_lip_cell("A", "")
    assert r.status == P.SELECT_SAME and ps.pose.curves["bs.x"] == 0.3


def test_unsaved_edit_prompts_in_every_direction_and_save_writes_curves_only():
    ps = make_set()
    ps.lipsync.select_lip_cell("A", "")
    ps.pose.set_curve("bs.new", 0.4)
    ps.pose.set_bone("jaw", m.BoneOffset(t=(0, 1, 0)))
    assert ps.lipsync.select_lip_cell("I", "").status == P.SELECT_NEEDS_CONFIRM
    assert ps.grid.select(0, 0).status == P.SELECT_NEEDS_CONFIRM
    assert ps.grid.confirm_select(P.CONFIRM_CANCEL).status == P.SELECT_CANCELLED and ps.ctx.selected_lip() == ("A", "")
    r = ps.lipsync.select_lip_cell("I", "")
    r = ps.lipsync.confirm_select_lip_cell(P.CONFIRM_SAVE)
    assert r.status == P.SELECT_SELECTED and r.saved and ps.ctx.selected_lip() == ("I", "")
    e = ps.ctx.doc.lip_sync.find_entry("A", "")
    assert e.curves == {"bs.a": 1.0, "bs.new": 0.4}
    assert not hasattr(e, "bones")


def test_save_message_mentions_ignored_bones_and_creates_entry():
    ps = make_set()
    ps.lipsync.select_lip_cell("I", "Joy")  # 行が無いマス
    assert ps.pose.curves == {} and not ps.pose.dirty
    ps.pose.set_curve("bs.i", 0.9)
    ps.pose.set_bone("jaw", m.BoneOffset(t=(0, 2, 0)))
    res = ps.pose.save()
    assert res.ok and "ボーン" in res.message
    assert ps.ctx.doc.lip_sync.find_entry("I", "Joy").curves == {"bs.i": 0.9}
    assert ps.pose.pose_to_apply().bones == {}
    ps.pose.zero()
    assert ps.pose.save().removed and ps.ctx.doc.lip_sync.find_entry("I", "Joy") is None


def test_switching_from_lip_to_key_and_point_prompts_and_discard():
    ps = make_set()
    ps.ctx.doc.perspective = m.Perspective(keys=[m.PerspectiveKey(30.0)])
    ps.lipsync.select_lip_cell("A", "")
    ps.pose.set_curve("bs.q", 0.2)
    assert ps.perspective.select_key(0).status == P.SELECT_NEEDS_CONFIRM
    r = ps.perspective.confirm_select_key(P.CONFIRM_DISCARD)
    assert r.status == P.SELECT_SELECTED and ps.ctx.lip_target is None and ps.ctx.selected_key() == 0
    assert "bs.q" not in ps.ctx.doc.lip_sync.find_entry("A", "").curves
    ps.pose.set_curve("bs.q", 0.2)
    assert ps.lipsync.select_lip_cell("A", "").status == P.SELECT_NEEDS_CONFIRM  # キー → マスでも確認


def test_layer_change_does_not_prompt_for_lip_edit():
    ps = make_set()
    ps.lipsync.select_lip_cell("A", "")
    ps.pose.set_curve("bs.x", 1.0)
    assert ps.layers.set_active(1).status == P.SELECT_SELECTED
    assert ps.pose.curves["bs.x"] == 1.0 and ps.ctx.selected_lip() == ("A", "")


def test_phoneme_rename_keeps_target_and_unsaved_edit():
    ps = make_set()
    ps.lipsync.select_lip_cell("A", "Joy")
    ps.pose.set_curve("bs.x", 0.5)
    assert ps.lipsync.rename_phoneme("A", "Aa").ok
    assert ps.ctx.selected_lip() == ("Aa", "Joy") and ps.pose.curves["bs.x"] == 0.5 and ps.pose.dirty
    assert ps.pose.save().ok and ps.ctx.doc.lip_sync.find_entry("Aa", "Joy").curves["bs.x"] == 0.5


def test_phoneme_remove_deselects():
    ps = make_set()
    ps.lipsync.select_lip_cell("A", "")
    assert ps.lipsync.remove_phoneme("A").ok
    assert ps.ctx.lip_target is None and not ps.pose.has_point


def test_layer_rename_follows_and_delete_removes_entries():
    ps = make_set()
    ps.lipsync.select_lip_cell("A", "Joy")
    ps.pose.set_curve("bs.x", 0.5)
    assert ps.layers.rename(1, "Happy").ok
    doc = ps.ctx.doc
    assert doc.lip_sync.find_entry("A", "Happy") is not None and doc.lip_sync.find_entry("A", "Joy") is None
    assert ps.ctx.selected_lip() == ("A", "Happy") and ps.pose.curves["bs.x"] == 0.5
    r = ps.layers.delete(1)
    assert r.ok and doc.lip_sync.find_entry("A", "Happy") is None
    assert ps.ctx.selected_lip() is None and len(doc.lip_sync.entries) == 2


def test_validation_issue_row_can_select_lip():
    ps = make_set()
    ps.ctx.doc.lip_sync.entries[0].curves["bs.a"] = 1.5
    ps.validation.run()
    rows = [r for g in ps.validation.view().groups for r in g.issues if r.code == "limit_exceeded"]
    assert rows and rows[0].lip == ("A", "") and rows[0].can_select_lip


# --- プロファイル ---


def test_bundled_profiles_have_lipsync_with_known_shapes():
    ps = prof.builtin_profiles()
    assert list(ps["shizuku"].lip_sync) == ["A", "I", "U", "E", "O"]
    assert ps["shizuku"].lip_sync["A"] == {"bs.lipSync_a": 1.0}
    assert "PP" in ps["vrchat_viseme"].lip_sync and ps["vrchat_viseme"].lip_sync["PP"] == {"vrc.v_pp": 1.0}
    assert ps["arkit52"].lip_sync["A"]["jawOpen"] == 0.8
    assert ps["metahuman"].lip_sync == {}
    for name in ("shizuku", "vrchat_viseme", "arkit52"):
        p = ps[name]
        for ph, shapes in p.lip_sync.items():
            assert all(s in p.standard_curves for s in shapes), (name, ph)


def test_profile_lipsync_read_write():
    p = prof.from_dict(
        {"format": "FacialNamingProfile", "name": "x", "lipSync": {"A": "a", "I": {"b": 0.5, "c": 1}, "": "z", "U": 3, "E": {}}}
    )
    assert p.lip_sync == {"A": {"a": 1.0}, "I": {"b": 0.5, "c": 1.0}}
    d = prof.to_dict(p)
    assert d["lipSync"] == {"A": "a", "I": {"b": 0.5, "c": 1.0}}
    assert "lipSync" not in prof.to_dict(prof.NamingProfile(name="y"))


def test_create_from_profile_resolves_names_and_reports():
    ps = make_set(lip=False)
    p = prof.from_dict(
        {"format": "FacialNamingProfile", "name": "x", "lipSync": {"A": {"jawOpen": 0.8, "mouthFunnel": 0.2}, "I": "bs.mouth_i", "U": "none"}}
    )
    scene = ["face.jawOpen", "bs.mouth_i", "face.mouthFunnel", "other.mouthFunnel"]
    r = ps.lipsync.create_from_profile(p, scene)
    assert r.ok and r.created == 2
    l = ps.ctx.doc.lip_sync
    assert l.enabled and l.phonemes == ["A", "I", "U"]
    assert l.find_entry("A", "").curves == {"face.jawOpen": 0.8, "face.mouthFunnel": 0.2}
    assert l.find_entry("I", "").curves == {"bs.mouth_i": 1.0}
    assert l.find_entry("U", "") is None and ("U", "none") in r.missing
    assert r.ambiguous == [("A", "mouthFunnel", "face.mouthFunnel")]
    # 2 回目: 行がある音素は残す
    r2 = ps.lipsync.create_from_profile(p, scene)
    assert r2.code == "nothing" and not r2.ok
    r3 = ps.lipsync.create_from_profile(p, scene, overwrite=True)
    assert r3.ok and r3.created == 2


def test_create_from_profile_without_scene_uses_written_names_and_fails_without_lipsync():
    ps = make_set(lip=False)
    p = prof.builtin_profiles()["shizuku"]
    assert ps.lipsync.create_from_profile(p).created == 5
    ps2 = make_set(lip=False)
    assert ps2.lipsync.create_from_profile(prof.NamingProfile(name="n")).code == "no_profile"
    assert ps2.ctx.doc.lip_sync is None


def test_create_from_profile_keeps_existing_base_rows_and_emotion_rows():
    ps = make_set()
    p = prof.from_dict({"format": "FacialNamingProfile", "name": "x", "lipSync": {"A": "zz", "U": "uu"}})
    r = ps.lipsync.create_from_profile(p)
    assert r.created == 1 and r.kept == ["A"]
    assert ps.ctx.doc.lip_sync.find_entry("A", "").curves == {"bs.a": 1.0}
    assert ps.ctx.doc.lip_sync.phonemes == ["A", "I", "U"]


# --- 試す ---


def test_try_evaluate_uses_profile_limits():
    ps = make_set()
    ps.ctx.doc.limits = {"bs.a": (0.0, 2.0)}
    ps.ctx.doc.lip_sync.volume = m.LipSyncVolume(0, 1, 2, 2)
    out = ps.lipsync.evaluate({"bs.a": 0.1}, {"A": 1.0})
    assert out["bs.a"] == 2.0 and out["bs.i"] == 0.0
    ps.ctx.doc.lip_sync.enabled = False
    assert ps.lipsync.evaluate({}, {"A": 1.0}) == {}
