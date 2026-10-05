"""シェイプの分類（core/categories.py）・プロファイルの categories・同梱プリセット・PoseView のタブ情報。"""

import json
from pathlib import Path

from tdrive_facial.core import categories as C
from tdrive_facial.core import profile as P

ROOT = Path(__file__).resolve().parents[2]
PRESET_DIR = ROOT / "maya" / "scripts" / "tdrive_facial" / "profiles"


def prof(*cats):
    return P.NamingProfile(name="t", categories=[C.Category(i, l, list(p)) for i, l, p in cats])


def test_pattern_kinds_and_case():
    assert C.pattern_matches("prefix:eye_", "eye_close_L")
    assert C.pattern_matches("prefix:EYE_", "eye_close_L")  # 大文字小文字は区別しない
    assert not C.pattern_matches("prefix:eye_", "Xeye_close")
    assert C.pattern_matches("contains:Close", "eye_close_L")
    assert C.pattern_matches("exact:jaw", "JAW")
    assert C.pattern_matches("jaw", "jaw") and not C.pattern_matches("jaw", "jaw_open")  # 接頭辞なし = 完全一致
    assert not C.pattern_matches("", "x") and not C.pattern_matches("prefix:", "x")


def test_node_prefix_is_stripped_and_lr_irrelevant():
    p = prof(("eye", "目", ["prefix:eye_"]))
    g = C.categorize(["bs.eye_close_L", "bs.eye_close_R", "eye_wide", "bs.mouth_a"], p)
    assert g[0] == ("eye", "目", ["bs.eye_close_L", "bs.eye_close_R", "eye_wide"])
    assert g[1] == (C.OTHER_ID, "その他", ["bs.mouth_a"])


def test_first_matching_category_wins_and_order_is_profile_order():
    p = prof(("a", "A", ["prefix:eye"]), ("look", "視線", ["prefix:eyeLook"]), ("b", "B", ["prefix:zzz"]))
    g = C.categorize(["eyeLookUp", "eyeBlink"], p)
    assert [(i, n) for i, _l, n in g] == [("a", ["eyeLookUp", "eyeBlink"])]
    p2 = prof(("look", "視線", ["prefix:eyeLook"]), ("eye", "目", ["prefix:eye"]))
    g2 = C.categorize(["eyeBlink", "eyeLookUp"], p2)
    assert [i for i, _l, _n in g2] == ["look", "eye"]  # 空の分類は出さない・プロファイルの順


def test_empty_names():
    assert C.categorize([], None) == []


def test_fallback_groups_by_head_token():
    names = ["bs.eye_a", "bs.eye_b", "bs.mouth_a", "bs.mouth_b", "bs.mouth_c", "bs.solo"]
    g = C.categorize(names, None)
    assert [(i, l) for i, l, _n in g] == [("auto:eye", "eye"), ("auto:mouth", "mouth"), (C.OTHER_ID, "その他")]
    assert g[2][2] == ["bs.solo"]  # 1 本だけの語は「その他」へ


def test_fallback_camel_case_head():
    names = ["eyeBlinkLeft", "eyeWideLeft", "jawOpen", "jawLeft", "mouthClose", "mouthFunnel"]
    g = C.categorize(names, None)
    assert [l for _i, l, _n in g] == ["eye", "jaw", "mouth"]
    assert C.head_token("smile") == "" and C.head_token("jawOpen") == "jaw" and C.head_token("a_b") == "a"


def test_fallback_too_few_or_too_many_groups_is_single_all():
    assert C.categorize(["a_1", "a_2", "b_1"], None) == [(C.ALL_ID, "すべて", ["a_1", "a_2", "b_1"])]  # 2 本以上の語が 1 つだけ
    many = [f"g{i}_{j}" for i in range(13) for j in range(2)]
    assert C.categorize(many, None) == [(C.ALL_ID, "すべて", many)]  # 13 グループ
    ok = [f"g{i}_{j}" for i in range(12) for j in range(2)]
    assert len(C.categorize(ok, None)) == 12


def test_profile_without_categories_uses_fallback():
    assert C.categorize(["x"], P.NamingProfile(name="t")) == [(C.ALL_ID, "すべて", ["x"])]


def test_profile_round_trip_is_byte_exact():
    p = prof(("eye", "目", ["prefix:eye_", "contains:x"]), ("m", "口", ["prefix:mouth_"]))
    p.categories[0].extra = {"note": 1}
    t = P.dumps(p)
    assert P.dumps(P.loads(t)) == t
    d = json.loads(t)
    assert list(d.keys())[-1] == "categories"
    assert d["categories"][0] == {"id": "eye", "label": "目", "patterns": ["prefix:eye_", "contains:x"], "note": 1}
    assert "categories" not in json.loads(P.dumps(P.NamingProfile(name="t")))  # 無いときは出さない


def test_profile_reader_skips_bad_entries():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = P.from_dict({"format": "FacialNamingProfile", "version": 1, "name": "t", "categories": [{"id": "a"}, {"label": "x"}, {"id": "a"}, 3]})
    assert [c.id for c in p.categories] == ["a"]


def test_bundled_profiles_round_trip():
    for f in PRESET_DIR.glob("*.fcprofile.json"):
        t = f.read_bytes().decode("utf-8")
        assert P.dumps(P.loads(t)) == t, f.name


def test_shizuku_every_standard_curve_in_a_real_category():
    p = P.load(PRESET_DIR / "shizuku.fcprofile.json")
    assert p.categories
    g = C.categorize(p.standard_curves, p)
    assert C.OTHER_ID not in [i for i, _l, _n in g], [n for i, _l, n in g if i == C.OTHER_ID]
    assert sum(len(n) for _i, _l, n in g) == len(p.standard_curves)
    labels = [l for _i, l, _n in g]
    assert {"目", "眉", "口", "あご", "頬", "視線", "リップシンク", "ハイライト", "その他の表現"} <= set(labels)
    by = {i: n for i, _l, n in g}
    assert "bs.LookingUp" in by["look"] and "bs.lipSync_a" in by["lipsync"] and "bs.specular_up" in by["specular"]


def test_arkit_and_vrchat_are_fully_categorized():
    a = P.load(PRESET_DIR / "arkit52.fcprofile.json")
    g = C.categorize(a.standard_curves, a)
    assert C.OTHER_ID not in [i for i, _l, _n in g] and sum(len(n) for _i, _l, n in g) == 52
    by = {i: n for i, _l, n in g}
    assert "eyeLookUpLeft" in by["look"] and "eyeBlinkLeft" in by["eye"] and "tongueOut" in by["tongue"]
    v = P.load(PRESET_DIR / "vrchat_viseme.fcprofile.json")
    assert [i for i, _l, _n in C.categorize(v.standard_curves, v)] == ["viseme"]


def test_metahuman_profile_has_categories_for_ctrl_names():
    m = P.load(PRESET_DIR / "metahuman.fcprofile.json")
    g = C.categorize(["CTRL_expressions_browDownL", "CTRL_expressions_jawOpen", "CTRL_expressions_mouthCloseD"], m)
    assert [i for i, _l, _n in g] == ["brow", "mouth", "jaw"]


# ---------------------------------------------------------------------------
# PoseView のタブ情報（presenters）
# ---------------------------------------------------------------------------


def _pose_set(names, profile):
    from tdrive_facial.core import model as m
    from tdrive_facial.core import presenters as PR

    d = m.Document()
    d.asset = "a"
    d.working_set = m.WorkingSet(curves=list(names), bones=[])
    d.layers[0].points[(1, 2)] = m.GridPoint(1, 2, True, m.SourcePose({names[0]: 0.4}))
    ps = PR.PresenterSet(d, profile=profile)
    ps.grid.select(1, 2)
    return ps


def test_pose_view_categories_counts_filter_and_changed():
    names = ["bs.eye_a", "bs.eye_b", "bs.mouth_a", "bs.mouth_b", "bs.solo"]
    p = prof(("eye", "目", ["prefix:eye_"]), ("mouth", "口", ["prefix:mouth_"]))
    ps = _pose_set(names, p)
    v = ps.pose.view()
    assert [(c.id, c.label, c.total, c.count) for c in v.categories] == [("eye", "目", 2, 2), ("mouth", "口", 2, 2), ("_other", "その他", 1, 1)]
    assert {r.name: r.category for r in v.curves}["bs.mouth_b"] == "mouth"
    assert [c.edited for c in v.categories] == [1, 0, 0] and [c.changed for c in v.categories] == [0, 0, 0]
    ps.pose.set_curve("bs.mouth_a", 0.5)
    v = ps.pose.view()
    assert [c.changed for c in v.categories] == [0, 1, 0] and {r.name: r.changed for r in v.curves}["bs.mouth_a"]
    ps.pose.set_filter("mouth")  # 文字列の絞り込みは件数だけ変える（タブは消えない）
    v = ps.pose.view()
    assert [(c.total, c.count) for c in v.categories] == [(2, 0), (2, 2), (1, 0)]


def test_pose_view_single_group_has_no_tabs():
    ps = _pose_set(["a", "b", "c"], None)
    assert ps.pose.view().categories == []
