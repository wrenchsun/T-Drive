"""名前付きプリセットの読み書き・検証（E-5。Maya / Qt 非依存）。"""

import json

import pytest

from tdrive_toon import look_layout as L
from tdrive_toon import params, presets


def _flat():
    return {"_ToonShadeColor": [0.1, 0.2, 0.3, 1.0], "_ToonOutlineWidth": 1.4, "_ToonRimStrength": 0.5, "common.albedoTint": [1, 1, 1, 1]}


def test_build_keeps_only_selected_groups():
    data = presets.build("影と線", ["Shadow", "Outline", "Nope"], _flat())
    assert data["schemaVersion"] == 1 and data["groups"] == ["Shadow", "Outline"] and data["label"] == "影と線"
    assert data["specific"] == {"_ToonShadeColor": [0.1, 0.2, 0.3, 1.0], "_ToonOutlineWidth": 1.4}
    assert "common" not in data and "renderQueueOffset" not in data


def test_save_load_roundtrip(tmp_path):
    data = presets.build("x", ["Shadow", "Common"], _flat())
    path = presets.save("影だけ", data, tmp_path)
    assert path == tmp_path / "presets" / "影だけ.json" and path.exists()
    p = presets.load(path)
    assert p.label == "x" and p.groups == ["Shadow", "Common"] and not p.warnings
    assert p.flat == {"_ToonShadeColor": [0.1, 0.2, 0.3, 1.0], "common.albedoTint": [1, 1, 1, 1]}


def test_load_drops_unknown_keys_with_warning(tmp_path):
    data = {"schemaVersion": 1, "label": "l", "groups": ["Shadow", "Bogus"],
            "specific": {"_ToonShadeColor": [0, 0, 0, 1], "_ToonNo": 1, "_ToonOutlineWidth": "a"}, "extra": 1}
    path = tmp_path / "presets" / "a.json"
    path.parent.mkdir()
    path.write_text(json.dumps(data), encoding="utf-8")
    p = presets.load(path)
    assert list(p.flat) == ["_ToonShadeColor"] and p.groups == ["Shadow"]
    assert len(p.dropped) == 2 and len(p.warnings) == 2


@pytest.mark.parametrize("text", ["{not json", "[1]", '{"label": "x"}', '{"schemaVersion": 99}', ""])
def test_broken_presets_raise_preset_error_only(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(presets.PresetError):
        presets.load(path)


def test_invalid_utf8_is_preset_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe\x00{")
    with pytest.raises(presets.PresetError):
        presets.load(path)


def test_list_presets_marks_broken_ones(tmp_path):
    assert presets.list_presets(tmp_path) == []
    presets.save("b", presets.build("Bee", ["Shadow"], _flat()), tmp_path)
    presets.save("a", presets.build("", ["Outline"], _flat()), tmp_path)
    (tmp_path / "presets" / "c.json").write_text("{oops", encoding="utf-8")
    entries = presets.list_presets(tmp_path)
    assert [e.name for e in entries] == ["a", "b", "c"]
    assert [e.ok for e in entries] == [True, True, False]
    assert entries[0].label == "a" and entries[1].label == "Bee" and entries[2].error


@pytest.mark.parametrize("bad", ["", "  ", "a/b", "a\\b", "..", ".x", "a:b", "x" * 81, "a?"])
def test_safe_name_rejects(bad):
    with pytest.raises(ValueError):
        presets.safe_name(bad)


def test_safe_name_accepts_japanese_and_strips_extension():
    assert presets.safe_name(" 影と線.json ") == "影と線"


def test_every_group_builds_a_loadable_preset(tmp_path):
    groups = list(L.build_groups())
    flat = {p.unity: p.default for p in params.SPECIFIC_PARAMS}
    path = presets.save("all", presets.build("all", groups, flat), tmp_path)
    p = presets.load(path)
    assert p.groups == groups and not p.warnings and set(p.flat) == set(flat)
