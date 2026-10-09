"""ルックのコピー / 貼り付けの検証と収集（E-4。Maya / Qt 非依存）。"""

import json

from tdrive_toon import look, params
from tdrive_toon import look_layout as L


def test_flat_payload_roundtrip():
    flat = {"_ToonOutlineWidth": 1.5, "common.albedoTint": [1, 0.5, 0.5, 1], "common.albedo": "tex/a.png", "renderQueueOffset": 3}
    payload = look.flat_to_payload(flat)
    assert payload == {
        "specific": {"_ToonOutlineWidth": 1.5},
        "common": {"albedoTint": [1, 0.5, 0.5, 1], "albedo": "tex/a.png"},
        "renderQueueOffset": 3,
    }
    ok, dropped = look.filter_pasteable(json.loads(json.dumps(payload)))
    assert ok == flat and dropped == []


def test_filter_drops_unknown_keys_and_wrong_types():
    payload = {
        "specific": {"_ToonShadeColor": [0.1, 0.2, 0.3, 1.0], "_ToonNoSuch": 1.0, "_ToonOutlineWidth": "thick",
                     "_ToonOutlineColor": [1, 2, 3]},
        "common": {"blend": "Weird", "doubleSided": 1, "cutoff": True, "nope": 1, "albedo": 5, "emissionColor": [0, 0, 0, 1]},
        "renderQueueOffset": 1.5,
        "extra": 1,
    }
    ok, dropped = look.filter_pasteable(payload)
    assert set(ok) == {"_ToonShadeColor", "common.emissionColor"}
    assert set(dropped) == {"extra", "_ToonNoSuch", "_ToonOutlineWidth", "_ToonOutlineColor", "common.blend", "common.doubleSided",
                            "common.cutoff", "common.nope", "common.albedo", "renderQueueOffset"}


def test_filter_handles_garbage():
    assert look.filter_pasteable([1, 2])[0] == {}
    ok, dropped = look.filter_pasteable({"specific": [1], "common": "x"})
    assert ok == {} and set(dropped) == {"specific", "common"}
    ok, dropped = look.filter_pasteable({"specific": {"_ToonOutlineWidth": float("nan")}})
    assert ok == {} and dropped == ["_ToonOutlineWidth"]


def test_filter_accepts_null_texture_and_valid_blend():
    ok, dropped = look.filter_pasteable({"common": {"albedo": None, "blend": "Cutout", "doubleSided": True}})
    assert ok == {"common.albedo": None, "common.blend": "Cutout", "common.doubleSided": True} and not dropped


def test_every_ui_key_is_pasteable_with_its_default():
    """UI の全行のキーが、既定値を貼り付けられる（契約と行の表がずれていない）。"""
    flat = {}
    for rows in L.build_groups().values():
        for key, *_ in rows:
            if key.startswith("common."):
                flat[key] = params.COMMON_FIELDS[key.split(".", 1)[1]]
            elif key == "renderQueueOffset":
                flat[key] = 0
            else:
                flat[key] = params.PARAMS_BY_UNITY[key].default
    ok, dropped = look.filter_pasteable(look.flat_to_payload(flat))
    assert not dropped and set(ok) == set(flat)


def test_restrict_keys():
    flat = {"a": 1, "b": 2, "c": 3}
    assert look.restrict_keys(flat, ["b", "z"]) == {"b": 2}


def test_collect_values_takes_first_and_copies():
    store = {"k1": [[1, 2], [3, 4]], "k2": [], "k3": [None]}
    out = L.collect_values(lambda k: store[k], ["k1", "k2", "k3"])
    assert out == {"k1": [1, 2], "k3": None}
    out["k1"].append(9)
    assert store["k1"][0] == [1, 2]


def test_keys_of_groups():
    keys = L.keys_of_groups(None, ["Shadow", "Nope"])
    assert "_ToonShadeColor" in keys and all(k in L.keys_of_groups(None, list(L.build_groups())) for k in keys)
