import pytest

from tdrive_toon import look, params, roles


def _sample():
    lk = look.new_look("unitychan", "assets/unitychan/unitychan.fbx")
    look.register_part(lk, "face", "face", ["face"])
    look.register_part(lk, "hair", "hair", ["hair"])
    return lk


def test_register_applies_role_preset():
    lk = _sample()
    face = lk["materials"]["face"]
    assert face["specific"]["_ToonShadowStrength"] == roles.ROLE_PRESETS["face"]["specific"]["_ToonShadowStrength"]
    assert lk["materials"]["hair"]["renderQueueOffset"] == 10
    assert look.validate(lk) == []


def test_material_moves_between_parts():
    lk = _sample()
    look.register_part(lk, "head", "face", ["face", "hair"])
    assert "hair" not in lk["parts"]  # 空になった部位は消える
    assert look.part_of(lk, "hair") == "head"


def test_variant_override_and_diff():
    lk = _sample()
    look.add_variant(lk, "B", "影を弱く")
    look.set_value(lk, "face", "_ToonShadowStrength", 0.1, variant="B")
    assert look.resolve(lk, "base")["face"]["specific"]["_ToonShadowStrength"] == 0.35
    assert look.resolve(lk, "B")["face"]["specific"]["_ToonShadowStrength"] == 0.1
    assert look.diff(lk, "base", "B") == [("face", "_ToonShadowStrength", 0.35, 0.1)]
    look.promote(lk, "B")
    assert lk["materials"]["face"]["specific"]["_ToonShadowStrength"] == 0.1
    assert lk["variants"] == {}


def test_upgrade_fills_new_params_with_defaults(tmp_path):
    """古い Look（P1 の項目が無い）を読むと既定値が補われ、検証を通る。"""
    lk = _sample()
    for mat in lk["materials"].values():
        mat["specific"].pop("_ToonRimStrength", None)
        mat["specific"].pop("_ToonDepthOffset", None)
    p = tmp_path / "old.json"
    p.write_text(look.dumps(lk), encoding="utf-8")
    loaded = look.load(p)
    assert loaded["materials"]["face"]["specific"]["_ToonRimStrength"] == 0.0
    assert loaded["materials"]["face"]["specific"]["_ToonDepthOffset"] == 0.0
    assert look.validate(loaded) == []


def test_roundtrip(tmp_path):
    lk = _sample()
    p = tmp_path / "look.json"
    look.save(lk, p)
    assert look.load(p) == lk
    assert p.read_text(encoding="utf-8") == look.dumps(lk)


def test_validation_rejects_unknown_and_reserved_names():
    lk = _sample()
    lk["materials"]["face"]["specific"]["_ZTest"] = 4  # D-Drive の描画ステート名は Specific に置けない
    lk["materials"]["face"]["common"]["blend"] = "Additive"
    errors = look.validate(lk)
    assert any("_ZTest" in e for e in errors)
    assert any("blend" in e for e in errors)
    with pytest.raises(KeyError):
        look.set_value(lk, "face", "_Unknown", 1)


def test_all_specific_params_are_toon_prefixed():
    reserved = {"_BaseMap", "_MainTex", "_BaseColor", "_Color", "_BumpMap", "_BumpScale", "_Cull", "_ZTest", "_ZWrite"}
    for p in params.SPECIFIC_PARAMS:
        assert p.unity.startswith("_Toon")
        assert p.unity not in reserved


def test_ddrive_material_data_export():
    lk = _sample()
    look.set_value(lk, "hair", "common.blend", "Cutout")
    md = look.to_ddrive_material_data(lk, "hair")
    assert md["Shader"] == params.UNITY_SHADER
    assert md["RenderQueue"] == 2450 + 10
    props = {s["Property"]: s["Value"] for s in md["Specific"]}
    assert props["_ToonOutlineWidth"] == {"Type": "Float", "FloatValue": 1.3}


@pytest.mark.parametrize(
    "name,role",
    [("face", "face"), ("eye_L1", "eye"), ("eyebase", "eye"), ("eyeline", "eyeline"),
     ("mat_cheek", "blush"), ("hair", "hair"), ("skin1", "skin"), ("body", "cloth"), ("Left", "other"),
     ("M_CharaA_Brow", "brow"), ("M_CharaA_Mouth", "mouth"), ("M_CharaA_Ribbon", "accessory")],
)
def test_guess_role(name, role):
    assert roles.guess_role(name) == role


def test_contract_matches_shader_spec_doc():
    """docs/03 §7.2 の表とパラメータ契約が一致している（仕様とコードのズレ防止）。"""
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[1] / "docs" / "03_shader_spec.md").read_text(encoding="utf-8")
    section = doc.split("### 7.2")[1].split("### 7.3")[0]
    import re
    documented = set(re.findall(r"^\| `(_Toon\w+)`", section, re.M))
    assert documented == {p.unity for p in params.SPECIFIC_PARAMS}


def test_reserved_names_not_in_use():
    assert not set(params.RESERVED_NAMES) & set(params.PARAMS_BY_UNITY)


def test_character_settings_defaults_and_validation():
    lk = _sample()
    assert lk[look.SETTINGS]["stencil"]["enabled"] is False
    assert lk["character"] == "unitychan"  # キャラクター ID とは別のセクション
    look.set_setting(lk, "innerLine.parts", ["hair"])
    look.set_setting(lk, "expressions", {"blush": [{"material": "face", "property": "_ToonTintStrength", "min": 0.0, "max": 0.8}]})
    assert look.validate(lk) == []
    assert look.expression_values(lk, "blush", 0.5) == [("face", "_ToonTintStrength", 0.4)]
    look.set_setting(lk, "innerLine.parts", ["nope"])
    look.set_setting(lk, "expressions", {"bad name": [{"material": "x", "property": "_ToonTintColor", "min": 0, "max": 1}]})
    errors = look.validate(lk)
    assert any("未登録の部位" in e for e in errors)
    assert any("英数字" in e for e in errors)
    assert any("material が未登録" in e for e in errors)
    assert any("Float のパラメータ契約" in e for e in errors)


def test_old_look_gets_character_settings(tmp_path):
    lk = _sample()
    del lk[look.SETTINGS]
    p = tmp_path / "old.json"
    p.write_text(look.dumps(lk), encoding="utf-8")
    assert look.load(p)[look.SETTINGS]["contactShadow"]["radius"] == 0.25


def test_every_param_and_setting_belongs_to_exactly_one_feature():
    from tdrive_toon import features

    owners = {}
    for f in features.FEATURES:
        for p in f.params:
            assert p not in owners, f"{p} が {owners.get(p)} と {f.id} の両方に属している"
            owners[p] = f.id
    assert set(owners) == {p.unity for p in params.SPECIFIC_PARAMS}
    assert set(features.FEATURE_OF_SETTING) == set(look.CHARACTER_DEFAULTS)


def test_feature_off_resolves_to_no_effect_and_keeps_saved_value():
    lk = _sample()
    look.set_feature(lk, "rim", True)
    look.set_value(lk, "hair", "_ToonRimStrength", 0.6)
    assert look.resolve(lk)["hair"]["specific"]["_ToonRimStrength"] == 0.6
    look.set_feature(lk, "rim", False)
    assert look.resolve(lk)["hair"]["specific"]["_ToonRimStrength"] == 0.0  # 効果なし
    assert lk["materials"]["hair"]["specific"]["_ToonRimStrength"] == 0.6  # 保存値は残る
    look.set_feature(lk, "outline", False)
    assert look.resolve(lk)["hair"]["specific"]["_ToonOutlineWidth"] == 0.0  # 既定値が効果ありのものは off_values
    md = look.to_ddrive_material_data(lk, "hair")
    props = {x["Property"] for x in md["Specific"]}
    assert "_ToonRimStrength" not in props and "_ToonOutlineWidth" not in props  # オフの機能は出力しない
    with pytest.raises(ValueError):
        look.set_feature(lk, "shade", False)


def test_old_look_turns_on_used_features():
    lk = _sample()
    look.set_value(lk, "hair", "_ToonRimStrength", 0.5)
    del lk[look.FEATURES]
    look.upgrade(lk)
    assert lk[look.FEATURES]["rim"] is True  # 使われていたのでオン（見た目が変わらない）
    assert lk[look.FEATURES]["matCap"] is False
    assert lk[look.FEATURES]["outline"] is True  # 既定でオン


def test_promote_does_not_bake_off_values():
    lk = _sample()
    look.set_value(lk, "hair", "_ToonRimStrength", 0.6)
    look.add_variant(lk, "B")
    look.set_value(lk, "hair", "_ToonShadeThreshold", 0.3, variant="B")
    look.promote(lk, "B")  # rim はオフのまま
    assert lk["materials"]["hair"]["specific"]["_ToonRimStrength"] == 0.6
    assert "features" not in lk["materials"]["hair"]


def test_line_part_keys_follow_sorted_part_names_and_line_flag():
    lk = _sample()
    parts = sorted(lk["parts"])
    first = parts[0]
    keys = look.line_part_keys(lk)
    assert all(k % 2 == 0 for k in keys.values())  # インナーラインがオフ: 線フラグなし
    look.set_feature(lk, "innerLine", True)
    look.set_setting(lk, "innerLine.enabled", True)
    look.set_setting(lk, "innerLine.parts", [first])
    keys = look.line_part_keys(lk)
    for i, part in enumerate(parts, start=1):
        for mat in lk["parts"][part]["materials"]:
            assert keys[mat] == i * 2 + (1 if part == first else 0)
    look.set_feature(lk, "innerLine", False)
    assert all(k % 2 == 0 for k in look.line_part_keys(lk).values())  # 機能オフでも線フラグなし


def test_normal_map_and_emission_features_resolve_common():
    """法線マップ・発光（Common）は機能オフなら既定値で解決（保存値は保持）。使っていれば古い Look でオンになる（4-10）。"""
    lk = _sample()
    mat = sorted(lk["materials"])[0]
    look.set_value(lk, mat, "common.emissionIntensity", 2.0)
    look.set_value(lk, mat, "common.normal", "textures/n.png")
    assert look.resolve(lk)[mat]["common"]["emissionIntensity"] == 0.0  # 既定でオフ
    assert look.resolve(lk)[mat]["common"]["normal"] is None
    assert lk["materials"][mat]["common"]["emissionIntensity"] == 2.0  # 保存値は残る
    look.set_feature(lk, "emission", True)
    assert look.resolve(lk)[mat]["common"]["emissionIntensity"] == 2.0
    md = look.to_ddrive_material_data(lk, mat)
    assert md["Common"]["EmissionIntensity"] == 2.0 and md["Common"]["Normal"] is None
    del lk[look.FEATURES]
    look.upgrade(lk)
    assert lk[look.FEATURES]["emission"] and lk[look.FEATURES]["normalMap"]  # 使っていたのでオン


def test_self_shadow_upgrade_keeps_look_and_face_does_not_receive():
    """セルフシャドウ（T-43）: 古い Look はオフのまま（見た目不変）。後でオンにしても顔は受けない（T-16）。"""
    lk = _sample()
    for m in lk["materials"].values():
        m["specific"].pop("_ToonReceiveShadow", None)  # この機能が入る前の Look
    lk.pop(look.FEATURES)
    look.upgrade(lk)
    assert lk[look.FEATURES]["selfShadow"] is False
    assert lk["materials"]["face"]["specific"]["_ToonReceiveShadow"] == 0.0  # ロールのプリセット
    assert lk["materials"]["hair"]["specific"]["_ToonReceiveShadow"] == 1.0
    assert look.resolve(lk)["hair"]["specific"]["_ToonReceiveShadow"] == 0.0  # オフは効果なし
    look.set_feature(lk, "selfShadow", True)
    assert look.resolve(lk)["hair"]["specific"]["_ToonReceiveShadow"] == 1.0
    assert look.resolve(lk)["face"]["specific"]["_ToonReceiveShadow"] == 0.0


def test_new_face_part_does_not_receive_self_shadow():
    lk = _sample()
    assert lk["materials"]["face"]["specific"]["_ToonReceiveShadow"] == 0.0
    assert lk[look.FEATURES]["selfShadow"] is False
    look.upgrade(lk)  # 開き直しても勝手にオンにならない（顔のプリセット 0 は「調整」ではない）
    assert lk[look.FEATURES]["selfShadow"] is False


def test_material_feature_overrides():
    """機能のマテリアル（シェーダー）単位の上書き（docs/11 §2.1、4-12）。"""
    lk = _sample()
    look.set_value(lk, "hair", "_ToonRimStrength", 0.5)
    look.set_value(lk, "face", "_ToonRimStrength", 0.5)
    look.set_material_feature(lk, "hair", "rim", True)  # 全体はオフのまま、髪だけオン
    res = look.resolve(lk)
    assert res["hair"]["specific"]["_ToonRimStrength"] == 0.5 and "rim" in res["hair"]["features"]
    assert res["face"]["specific"]["_ToonRimStrength"] == 0.0 and "rim" not in res["face"]["features"]
    assert "rim" in look.enabled_features(lk)  # 和集合（シェーダーの組み合わせに入る）
    assert look.FEATURE_OVERRIDES not in res["hair"]  # 解決結果には上書きのデータを残さない
    look.set_feature(lk, "rim", True)
    look.set_material_feature(lk, "face", "rim", False)  # 全体オン、顔だけオフ
    assert not look.material_enabled(lk, "face", "rim") and look.material_enabled(lk, "hair", "rim")
    look.set_material_feature(lk, "hair", "rim", None)  # 全体に従う
    assert look.material_override(lk, "hair", "rim") is None and look.FEATURE_OVERRIDES not in lk["materials"]["hair"]
    assert look.validate(lk) == []
    with pytest.raises(ValueError):
        look.set_material_feature(lk, "hair", "contactShadow", True)  # キャラクター単位の機能
    with pytest.raises(ValueError):
        look.set_material_feature(lk, "hair", "shade", False)  # 必須
    lk["materials"]["hair"][look.FEATURE_OVERRIDES] = {"innerLine": True}
    assert any("キャラクター単位" in e for e in look.validate(lk))


def test_material_overrides_survive_promote_and_are_not_in_variants():
    lk = _sample()
    look.set_material_feature(lk, "hair", "matCap", True)
    look.add_variant(lk, "B")
    look.promote(lk, "B")
    assert look.material_override(lk, "hair", "matCap") is True
    lk["variants"] = {"C": {"label": "", "overrides": {"hair": {look.FEATURE_OVERRIDES: {"rim": True}}}}}
    assert any("バリアントでは使えない" in e for e in look.validate(lk))


def test_self_shadow_cast_is_per_material_and_off_resolves_zero():
    """セルフシャドウを落とす（4-13）: _ToonCastShadow は 0/1、機能オフで 0（影を受けも落としもしない）。"""
    lk = _sample()
    assert lk["materials"]["hair"]["specific"]["_ToonCastShadow"] == 1.0
    assert look.resolve(lk)["hair"]["specific"]["_ToonCastShadow"] == 0.0  # 既定で機能オフ
    look.set_feature(lk, "selfShadow", True)
    look.set_value(lk, "hair", "_ToonCastShadow", 0.0)  # 髪は落とさない
    res = look.resolve(lk)
    assert res["hair"]["specific"]["_ToonCastShadow"] == 0.0 and res["hair"]["specific"]["_ToonReceiveShadow"] == 1.0
    assert params.PARAMS_BY_UNITY["_ToonCastShadow"].toggle


def test_see_through_outline_setting_param_and_feature():
    """透かし線（T-44）: 部位ごとの 0/1・キャラクター設定・機能。機能オフなら 0（手前に出す量をそのまま使う）。"""
    lk = _sample()
    assert lk[look.SETTINGS]["seeThroughOutline"] == {"width": 1.5, "color": [0.25, 0.15, 0.15, 1.0], "maxDistance": 0.1, "occluders": []}
    assert params.PARAMS_BY_UNITY["_ToonSeeThroughOutline"].toggle
    look.set_value(lk, "face", "_ToonSeeThroughOutline", 1.0)
    assert look.resolve(lk)["face"]["specific"]["_ToonSeeThroughOutline"] == 0.0  # 既定で機能オフ
    look.set_feature(lk, "seeThroughOutline", True)
    assert look.resolve(lk)["face"]["specific"]["_ToonSeeThroughOutline"] == 1.0
    assert look.validate(lk) == []
    # 手前の部位: 未指定なら髪ロール（部位番号 = 名前順 1 始まり: face=1, hair=2 → ビット 1）
    assert look.see_through_occluders(lk) == ["hair"] and look.see_through_occluder_mask(lk) == 0b10
    look.set_setting(lk, "seeThroughOutline.occluders", ["face"])
    assert look.see_through_occluder_mask(lk) == 0b01
    look.set_setting(lk, "seeThroughOutline.occluders", ["nope"])
    assert any("occluders に未登録の部位" in e for e in look.validate(lk))
    look.set_setting(lk, "seeThroughOutline.occluders", [])
    look.set_setting(lk, "seeThroughOutline.maxDistance", -1.0)
    look.set_setting(lk, "seeThroughOutline.color", [1, 0, 0])
    errors = look.validate(lk)
    assert any("seeThroughOutline.maxDistance" in e for e in errors)
    assert any("seeThroughOutline.color" in e for e in errors)
