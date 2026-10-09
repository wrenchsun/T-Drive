"""ルックタブの並び・見出しの表（look_layout.py。Maya / Qt 非依存。E-2〜E-9）。"""

from tdrive_toon import features, params
from tdrive_toon import look_layout as L


def _all_groups():
    return L.build_groups()


def test_group_features_exist_and_cover_all_groups():
    groups = _all_groups()
    assert set(L.GROUP_FEATURE) == set(groups)  # 表に無いグループ・グループの無い表が無い
    for group, fid in L.GROUP_FEATURE.items():
        assert fid is None or fid in features.BY_ID, (group, fid)
    assert L.GROUP_FEATURE["Shadow"] == "shade" and features.BY_ID["shade"].required
    assert L.GROUP_FEATURE["Common"] is None and L.GROUP_FEATURE["Textures"] is None


def test_sub_features_exist_and_are_not_representatives():
    reps = {f for f in L.GROUP_FEATURE.values() if f}
    seen = set()
    for group, subs in L.SUB_FEATURES.items():
        assert group in L.GROUP_FEATURE
        for fid in subs:
            assert fid in features.BY_ID and fid not in reps and fid not in seen, fid
            seen.add(fid)


def test_every_row_belongs_to_group_rep_sub_or_nothing():
    """行の機能は features.FEATURE_OF_* から導く。見出しに現れない機能が無いこと（行が迷子にならない）。"""
    for group, rows in _all_groups().items():
        allowed = {L.GROUP_FEATURE[group], *L.SUB_FEATURES.get(group, ())}
        for row in rows:
            fid = L.feature_of_key(row[0])
            assert fid is None or fid in allowed, (group, row[0], fid)


def test_every_material_scope_feature_appears_in_a_heading():
    shown = {f for f in L.GROUP_FEATURE.values() if f} | {f for subs in L.SUB_FEATURES.values() for f in subs}
    missing = [f.id for f in features.FEATURES if f.material_scope and f.id not in shown]
    assert not missing, missing


def test_every_param_with_feature_has_a_row():
    keys = {r[0] for rows in _all_groups().values() for r in rows}
    assert set(features.FEATURE_OF_PARAM) <= keys
    assert {f"common.{c}" for c in features.FEATURE_OF_COMMON} <= keys


def test_split_group_keeps_all_rows():
    for group, rows in _all_groups().items():
        main, subs = L.split_group(group, rows)
        assert len(main) + sum(len(r) for _f, r in subs) == len(rows)
        assert [f for f, _ in subs] == [f for f in L.SUB_FEATURES.get(group, ()) if any(L.feature_of_key(r[0]) == f for r in rows)]


def test_anchors_exist_in_manual():
    html = (L.__file__.rsplit("maya", 1)[0] + "docs/DesignerManual/" + L.MANUAL_PAGE).replace("\\", "/")
    text = open(html, encoding="utf-8").read()
    anchors = {L.anchor_of(g) for g in L.GROUP_FEATURE} | set(L.SUB_ANCHOR.values())
    missing = [a for a in anchors if a and f'id="{a}"' not in text]
    assert not missing, missing


def test_prefs_are_noop_without_maya():
    L.pref_set("tdriveLookSection_Test", 0)  # 例外にならない
    assert L.pref_get("tdriveLookSection_Test", 1) == 1
