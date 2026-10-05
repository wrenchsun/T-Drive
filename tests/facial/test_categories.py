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


def test_default_groups_by_text_before_first_underscore():
    names = ["bs.eye_a", "bs.eye_b", "bs.mouth_a", "bs.mouth_b", "bs.mouth_c", "bs.solo"]
    g = C.categorize(names, None)
    assert [(i, l) for i, l, _n in g] == [("eye", "eye"), ("mouth", "mouth"), (C.OTHER_ID, "その他")]
    assert g[2][2] == ["bs.solo"]  # `_` が無い名前は「その他」へ
    assert C.prefix_of("bs.eye_close_L") == "eye" and C.prefix_of("jawOpen") == "" and C.prefix_of("_x") == ""


def test_default_groups_no_camel_case_split():
    names = ["eyeBlinkLeft", "eyeWideLeft", "jawOpen", "jawLeft", "mouthClose", "mouthFunnel"]  # ARKit 風: `_` が無い
    assert C.categorize(names, None) == [(C.ALL_ID, "すべて", names)]


def test_default_groups_case_insensitive_label_most_frequent_spelling():
    names = ["Eye_x", "eye_y", "eye_z", "mouth_a", "MOUTH_b"]
    g = C.categorize(names, None)
    assert [(i, l, n) for i, l, n in g] == [("eye", "eye", ["Eye_x", "eye_y", "eye_z"]), ("mouth", "mouth", ["mouth_a", "MOUTH_b"])]  # 同数なら先に出た綴り
    assert C.categorize(["Eye_x", "Eye_y", "eye_z", "m_a", "m_b"], None)[0][1] == "Eye"


def test_default_groups_order_singles_and_no_upper_limit():
    names = ["b_1", "a_1", "z_1", "a_2", "b_2", "solo_1", "plain", "z_2"]
    g = C.categorize(names, None)
    assert [i for i, _l, _n in g] == ["b", "a", "z", C.OTHER_ID]  # 最初に出た順・「その他」は最後
    assert g[-1][2] == ["solo_1", "plain"]  # 1 本だけの接頭辞 + `_` なし（一覧の順）
    many = [f"g{i}_{j}" for i in range(40) for j in range(2)]
    assert len(C.categorize(many, None)) == 40  # 上限なし


def test_default_groups_fewer_than_two_groups_is_single_all():
    assert C.categorize(["a_1", "a_2", "b_1"], None) == [(C.ALL_ID, "すべて", ["a_1", "a_2", "b_1"])]  # 2 本以上の語が 1 つだけ
    assert C.categorize(["a", "b"], None) == [(C.ALL_ID, "すべて", ["a", "b"])]


def test_profile_without_categories_uses_default():
    assert C.categorize(["x"], P.NamingProfile(name="t")) == [(C.ALL_ID, "すべて", ["x"])]


def test_profile_categories_still_override_default():
    p = prof(("e", "目", ["prefix:eye_"]))
    g = C.categorize(["eye_a", "mouth_a", "mouth_b"], p)
    assert g == [("e", "目", ["eye_a"]), (C.OTHER_ID, "その他", ["mouth_a", "mouth_b"])]


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


def test_bundled_profiles_have_no_categories_block():
    for f in PRESET_DIR.glob("*.fcprofile.json"):
        assert "categories" not in json.loads(f.read_text(encoding="utf-8")), f.name  # 既定の `_` の規則を上書きしない


def test_shizuku_standard_curves_one_tab_per_prefix():
    p = P.load(PRESET_DIR / "shizuku.fcprofile.json")
    assert not p.categories
    g = C.categorize(p.standard_curves, p)
    assert [(i, l, len(n)) for i, l, n in g] == [
        ("lipsync", "lipSync", 5), ("look", "look", 7), ("specular", "specular", 6), ("eye", "eye", 12), ("brow", "brow", 7),
        ("jaw", "jaw", 4), ("mouth", "mouth", 24), ("cheek", "cheek", 3), ("other", "other", 5), (C.OTHER_ID, "その他", 2),
    ]
    by = {i: n for i, _l, n in g}
    assert by[C.OTHER_ID] == ["bs.LookingUp", "bs.LookingDown"]  # `_` が無い名前
    assert "bs.look_left" in by["look"] and "bs.lipSync_a" in by["lipsync"] and "bs.specular_up" in by["specular"]
    assert sum(len(n) for _i, _l, n in g) == len(p.standard_curves)


def test_arkit_and_vrchat_and_metahuman_have_no_tabs():
    for name in ("arkit52", "vrchat_viseme", "metahuman"):
        p = P.load(PRESET_DIR / f"{name}.fcprofile.json")
        g = C.categorize(p.standard_curves or ["CTRL_expressions_browDownL", "CTRL_expressions_jawOpen"], p)
        assert C.is_trivial(g), name  # `_` の前が 1 種類・`_` が無い → 「すべて」だけ


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


def test_pose_view_default_prefix_tabs_follow_working_set_rows():
    from tdrive_facial.core import model as m
    names = ["bs.eye_a", "bs.eye_b", "bs.mouth_a", "bs.mouth_b", "bs.cheek_a", "bs.cheek_b"]
    ps = _pose_set(names, None)
    v = ps.pose.view()
    assert [(c.id, c.count) for c in v.categories] == [("eye", 2), ("mouth", 2), ("cheek", 2)]
    # 作業セットを eye + mouth に絞る（作業セットだけ = 既定）: タブも件数も、一覧に出る行だけで決まる
    ps.ctx.doc.working_set = m.WorkingSet(curves=["bs.eye_a", "bs.eye_b", "bs.mouth_a", "bs.mouth_b"], bones=[])
    ps.pose.set_working_set_only(True)
    v = ps.pose.view()
    assert [(c.id, c.count) for c in v.categories] == [("eye", 2), ("mouth", 2)]
    assert {r.name for r in v.curves} == {"bs.eye_a", "bs.eye_b", "bs.mouth_a", "bs.mouth_b"} and all(r.category for r in v.curves)
    ps.pose.set_filter("eye")  # 文字列の絞り込みは件数だけ変える（0 本のタブを隠すのは UI の仕事）
    assert [(c.id, c.count) for c in ps.pose.view().categories] == [("eye", 2), ("mouth", 0)]
    ps.pose.set_filter("")
    ps.ctx.doc.working_set = m.WorkingSet(curves=["bs.eye_a", "bs.eye_b", "bs.mouth_a", "bs.cheek_a"], bones=[])
    assert ps.pose.view().categories == []  # 接頭辞のグループが 1 つだけ（mouth・cheek は 1 本ずつで「その他」）→ タブなし
