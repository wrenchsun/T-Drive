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


def test_search_matches_label_key_and_help():
    assert L.matches("", "_ToonOutlineWidth", "線幅")
    assert L.matches("線", "_ToonOutlineWidth", "線幅")
    assert L.matches("outlinewidth", "_ToonOutlineWidth", "線幅")  # 大文字小文字を無視・Unity 名
    assert L.matches("  線  ", "_ToonOutlineWidth", "線幅")
    assert L.matches("顔の向き", "_ToonFacialAngle", "角度連動")  # PARAM_HELP
    assert not L.matches("リム", "_ToonOutlineWidth", "線幅")


def test_search_senn_shows_only_outline_group_rows():
    hits = {g: [r[0] for r in rows if L.row_passes(r[0], r[1], "線")] for g, rows in L.build_groups().items()}
    assert len(hits["Outline"]) >= 5  # 線色・線幅など
    assert not hits["Rim"] and not hits["Shadow"] and not hits["Color"] and not hits["Tint"]  # 部分一致なので「法線」「視線」の行は混ざる


def test_simple_keys_exist_in_contract_and_rows():
    rows = {r[0] for rr in L.build_groups().values() for r in rr}
    assert len(L.SIMPLE_KEYS) == 13 and len(set(L.SIMPLE_KEYS)) == 13
    for k in L.SIMPLE_KEYS:
        assert k in rows, k
        if k.startswith("_Toon"):
            assert k in params.PARAMS_BY_UNITY, k  # 契約に実在する


def test_simple_mode_filters_rows_but_search_looks_in_both():
    rows = [r for rr in L.build_groups().values() for r in rr]
    simple = [r[0] for r in rows if L.row_passes(r[0], r[1], "", L.MODE_SIMPLE)]
    assert simple == [k for k in (r[0] for r in rows) if k in L.SIMPLE_KEYS]
    assert len([r for r in rows if L.row_passes(r[0], r[1], "", L.MODE_DETAIL)]) == len(rows)
    # 検索中はモードを無視（かんたんでも詳細の項目が見つかる）
    assert L.row_passes("_ToonRimPower", "リムの鋭さ", "鋭さ", L.MODE_SIMPLE)
    assert not L.row_passes("_ToonRimPower", "リムの鋭さ", "", L.MODE_SIMPLE)


def test_parse_color_hex_and_floats():
    assert L.parse_color("#8070a0") == [round(0x80 / 255, 4), round(0x70 / 255, 4), round(0xA0 / 255, 4), 1.0]
    assert L.parse_color("8070A0") == L.parse_color("#8070a0")
    assert L.parse_color("#80706040")[3] == round(0x40 / 255, 4)
    assert L.parse_color("#8070a0", alpha=0.5)[3] == 0.5  # アルファ省略時は元のアルファ
    assert L.parse_color("0.5, 0.25, 1") == [0.5, 0.25, 1.0, 1.0]
    assert L.parse_color("(0.5 0.25 1 0.5)") == [0.5, 0.25, 1.0, 0.5]


def test_parse_color_rejects_garbage():
    for bad in ("", "red", "#12345", "#GGGGGG", "1, 2", "1.5, 0, 0", "-0.1, 0, 0", "0.1, 0.2, 0.3, 0.4, 0.5", "nan, 0, 0"):
        assert L.parse_color(bad) is None, bad


def test_color_roundtrip():
    assert L.format_hex([1, 0.5, 0, 1]) == "#FF8000"
    assert L.format_hex([1, 0.5, 0, 0.5]) == "#FF800080"
    assert L.format_floats([1.0, 0.5, 0.0, 1.0]) == "1, 0.5, 0, 1"
    for c in ([0.0, 0.0, 0.0, 1.0], [1.0, 1.0, 1.0, 1.0], [0.502, 0.4392, 0.6275, 1.0]):
        assert L.parse_color(L.format_hex(c)) == [round(round(x * 255) / 255, 4) for x in c]
        assert L.parse_color(L.format_floats(c)) == c


# ---------------------------------------------------------------- 影色をベース色から作る（E-6）
def test_shade_from_white_is_close_to_preset_shade_color():
    out = L.shade_from_base([1.0, 1.0, 1.0])
    preset = params.PARAMS_BY_UNITY["_ToonShadeColor"].default
    assert len(out) == 4 and out[3] == 1.0
    assert all(abs(a - b) <= 0.15 for a, b in zip(out[:3], preset[:3])), (out, preset)


def test_shade_pair_second_is_darker_again():
    shade, shade2 = L.shade_pair([1.0, 0.9, 0.8], *L.SHADE_DEFAULTS)
    assert shade2[:3] != shade[:3]
    assert max(shade2[:3]) <= max(shade[:3])
    assert shade2 == L.shade_from_base(shade, *L.SHADE_DEFAULTS)


def test_shade_hue_shift_wraps_and_values_clamp():
    red = L.shade_from_base([1.0, 0.0, 0.0], 0.5, 1.0, 1.0)  # 赤 → 色相を半周 → シアン
    assert red[0] < 0.01 and red[1] > 0.99 and red[2] > 0.99
    assert L.shade_from_base([0.5, 0.2, 0.2], 0.0, 9.0, 9.0)[:3] == [1.0, 0.0, 0.0]
    assert L.shade_from_base([0.5, 0.2, 0.2], 0.0, 1.0, 0.0)[:3] == [0.0, 0.0, 0.0]
    assert L.shade_from_base([2.0, -1.0, 0.5], 0.0, 1.0, 1.0)[:3] == L.shade_from_base([1.0, 0.0, 0.5], 0.0, 1.0, 1.0)[:3]


def test_identity_shade_keeps_color():
    assert L.shade_from_base([0.3, 0.6, 0.9], 0.0, 1.0, 1.0)[:3] == [0.3, 0.6, 0.9]


def test_shade_defaults_within_ranges():
    for v, (lo, hi) in zip(L.SHADE_DEFAULTS, L.SHADE_RANGES):
        assert lo <= v <= hi


def test_average_rgb():
    assert L.average_rgb([(1, 0, 0, 1), (0, 0, 1, 1)]) == [0.5, 0.0, 0.5]
    assert L.average_rgb([(1, 0, 0, 0), (0, 1, 0, 1)]) == [0.0, 1.0, 0.0]  # 透明は数えない
    assert L.average_rgb([]) == [1.0, 1.0, 1.0]


# ---------------------------------------------------------------- テクスチャのドロップ（E-10）
def test_path_from_uri():
    assert L.path_from_uri("file:///C:/proj/tex/a%20b.png") == "C:/proj/tex/a b.png"
    assert L.path_from_uri("file://server/share/x.png") == "//server/share/x.png"
    assert L.path_from_uri("file:///home/me/x.png") == "/home/me/x.png"
    assert L.path_from_uri("https://example.com/x.png") is None
    assert L.path_from_uri("# comment") is None and L.path_from_uri("") is None


def test_first_image_from_uri_list_skips_non_images():
    text = "# c\r\nfile:///C:/a/readme.txt\r\nfile:///C:/a/hair.TGA\r\nfile:///C:/a/b.png\r\n"
    assert L.first_image_from_uri_list(text) == "C:/a/hair.TGA"
    assert L.first_image_from_uri_list("file:///C:/a/x.txt") is None
    assert L.is_image_path("a.PNG") and not L.is_image_path("a.fbx")


# ---------------------------------------------------------------- E-11 / E-12
def _follow_look():
    from tdrive_toon import look

    lk = look.new_look("unitychan", "assets/unitychan/unitychan.fbx")
    look.register_part(lk, "face", "face", ["face"])
    look.register_part(lk, "hair", "hair", ["hair"])
    look.register_part(lk, "cloth", "cloth", ["body", "skirt"])
    return lk


def test_follow_target_part_when_single_material_else_material():
    lk = _follow_look()
    assert L.follow_target(lk, [], ["hair"]) == "hair"  # 部位内が 1 つ → 部位
    assert L.follow_target(lk, [], ["skirt"]) == "skirt"  # 複数 → マテリアル
    assert L.follow_target(lk, [], ["body", "skirt"]) == "body"  # 複数を選んでいたら先頭


def test_follow_target_face_selection_wins_and_unknown_is_ignored():
    lk = _follow_look()
    assert L.follow_target(lk, ["face"], ["hair", "skirt"]) == "face"  # フェースのマテリアルを優先
    assert L.follow_target(lk, ["unknown"], ["hair"]) == "hair"  # フェース側が未登録ならオブジェクト側
    assert L.follow_target(lk, ["unknown"], ["nope"]) is None  # どれも未登録 → 何もしない
    assert L.follow_target(lk, [], []) is None
    assert L.follow_target(None, ["hair"], ["hair"]) is None
    assert L.follow_target({}, ["hair"], []) is None


def test_targets_title_and_chip_rgb():
    assert L.targets_title(["hair", "cloth"]) == "2 件を編集中: hair, cloth"
    assert L.targets_title(["a", "b", "c", "d"]) == "4 件を編集中: a, b, c, …"
    assert L.chip_rgb([1, 0.5, 0, 1]) == (255, 128, 0)
    assert L.chip_rgb([2, -1, 0.2]) == (255, 0, 51)  # 範囲外は丸める
    assert L.chip_rgb(None) == (255, 255, 255) and L.chip_rgb([1]) == (255, 255, 255)
    assert L.FOLLOW_PREF == "tdriveLookFollowSelection"
