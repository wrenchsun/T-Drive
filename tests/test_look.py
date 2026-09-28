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
