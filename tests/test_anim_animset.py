"""animset.json の読み書き・検証（docs/20 §3.3。Maya 非依存）。"""

import json
from pathlib import Path

import pytest

from tdrive_anim.core import animset as A


def make():
    d = A.new_animset("MisotenChara")
    A.add_clip(d, A.default_clip("A_MisotenChara_Idle", 0, 60, "MisotenChara"))
    A.add_clip(d, A.default_clip("A_MisotenChara_Jump", 100, 118, "MisotenChara"))
    return d


def test_default_clip_by_action():
    idle = A.default_clip("A_MisotenChara_Idle", 0, 60, "MisotenChara")
    assert idle["loop"] is True and idle["stateGroup"] == "locomotion" and idle["rootMotion"] == "none"
    run = A.default_clip("A_MisotenChara_Run_F", 0, 18, "MisotenChara")
    assert run["loop"] and run["stateGroup"] == "locomotion"
    jump = A.default_clip("A_MisotenChara_Jump", 0, 18, "MisotenChara")
    assert jump["loop"] is False and jump["stateGroup"] == "oneshot"
    assert A.action_of("A_MisotenChara_Pose01", "MisotenChara") == "Pose"


def test_roundtrip(tmp_path: Path):
    d = make()
    p = A.save(tmp_path / "anim" / "MisotenChara" / "animset.json", d)
    assert p.read_text(encoding="utf-8").endswith("}\n")
    assert A.load(p) == d
    assert A.validate(d) == []


def test_unknown_keys_survive():
    raw = json.loads(A.dumps(make()))
    raw["futureKey"] = {"a": 1}
    raw["clips"][0]["futureClipKey"] = 7
    d = A.loads(json.dumps(raw))
    assert d["futureKey"] == {"a": 1} and d["clips"][0]["futureClipKey"] == 7
    assert json.loads(A.dumps(d))["clips"][0]["futureClipKey"] == 7


def test_missing_keys_get_defaults():
    d = A.loads(json.dumps({"format": "TDriveAnimSet", "modelId": "X", "clips": [{"name": "A_X_Run", "start": 1, "end": 5}]}))
    assert d["frameRate"] == 30 and d["clips"][0]["loop"] is True and d["export"]["category"] == "Player"
    assert [l["name"] for l in d["layers"]] == ["Base", "UpperBody"]


def test_broken_json_and_wrong_format():
    with pytest.raises(A.AnimSetError):
        A.loads("{not json")
    with pytest.raises(A.AnimSetError):
        A.loads('{"format": "Other"}')
    with pytest.raises(A.AnimSetError):
        A.load("no/such/file.json")


def test_duplicate_name_is_error():
    d = make()
    with pytest.raises(A.AnimSetError):
        A.add_clip(d, A.default_clip("A_MisotenChara_Idle", 0, 5, "MisotenChara"))
    d["clips"].append(dict(d["clips"][0]))  # 手で書き換えられたファイル
    errs = A.errors(d)
    assert any("重複" in e.message for e in errs)


@pytest.mark.parametrize(
    "mutate, path",
    [
        (lambda d: d.update(modelId="misoten"), "modelId"),
        (lambda d: d.update(frameRate=24), "frameRate"),
        (lambda d: d.update(units="m"), "units"),
        (lambda d: d["clips"][0].update(start=10, end=5), "clips[0].end"),
        (lambda d: d["clips"][0].update(layer="Nope"), "clips[0].layer"),
        (lambda d: d["clips"][0].update(rootMotion="x"), "clips[0].rootMotion"),
        (lambda d: d["clips"][0].update(crossFade=-1), "clips[0].crossFade"),
        (lambda d: d["clips"][0].update(start="a"), "clips[0].start"),
        (lambda d: d["clips"][0].update(events=[{"frame": "x"}]), "clips[0].events[0]"),
    ],
)
def test_validation_errors(mutate, path):
    d = make()
    mutate(d)
    assert any(i.level == "error" and i.path == path for i in A.validate(d)), A.validate(d)


def test_event_outside_range_is_warning():
    d = make()
    d["clips"][0]["events"] = [{"frame": 999, "key": "OnFootPlant", "args": 0}]
    issues = A.validate(d)
    assert [i.level for i in issues] == ["warning"]
    assert A.errors(d) == []


def test_newer_version_warns():
    d = make()
    d["version"] = 99
    assert any(i.path == "version" and i.level == "warning" for i in A.validate(d))


def test_remove_clip():
    d = make()
    assert A.remove_clip(d, "A_MisotenChara_Idle") and not A.remove_clip(d, "A_MisotenChara_Idle")
    assert len(d["clips"]) == 1


def test_schema_file_matches_keys():
    schema = json.loads((Path(A.__file__).parent / "animset_schema.json").read_text(encoding="utf-8"))
    d = A.new_animset("X")
    assert set(schema["required"]) <= set(d)
    clip = A.default_clip("A_X_Idle", 0, 1, "X")
    assert set(schema["properties"]["clips"]["items"]["properties"]) == set(clip)
