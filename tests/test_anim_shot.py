"""ショットの読み書き・検証（docs/20 §5.1。Maya 非依存）。"""

import json
from pathlib import Path

import pytest

from tdrive_anim.core import shot as S


def make():
    d = S.new_shot("Opening01", 30, 0, 240)
    S.add_camera(d, "CAM_Main", 0, 120)
    S.add_camera(d, "CAM_Close", 121, 240)
    S.add_character(d, "MisotenChara", sequence=[{"clip": "A_MisotenChara_Idle", "holdSec": 2.0, "crossFadeSec": 0.1}, {"clip": "A_MisotenChara_Wave"}])
    S.add_event(d, 60, "Hit")
    return d


def test_roundtrip(tmp_path: Path):
    d = make()
    p = S.save(S.path_for(tmp_path / "shots", "Opening01"), d)
    assert p.name == "Opening01.shot.json" and p.read_text(encoding="utf-8").endswith("}\n")
    assert S.load(p) == d
    assert S.validate(d) == []


def test_new_shot_rules():
    for bad in ("opening", "Open ing", "Open__ing", "", "1st", "オープニング"):
        with pytest.raises(S.ShotError):
            S.new_shot(bad)
    with pytest.raises(S.ShotError):
        S.new_shot("A", 24)
    with pytest.raises(S.ShotError):
        S.new_shot("A", 30, 10, 5)
    assert S.new_shot("A", 60)["frameRate"] == 60


def test_unknown_keys_and_defaults():
    raw = json.loads(S.dumps(make()))
    raw["future"] = 1
    raw["characters"][0]["futureKey"] = 2
    del raw["props"]
    d = S.loads(json.dumps(raw))
    assert d["future"] == 1 and d["characters"][0]["futureKey"] == 2 and d["props"] == []
    d2 = S.loads(json.dumps({"format": "TDriveShot", "shot": "A", "frameRate": 30, "range": [0, 1], "characters": [{"modelId": "X"}]}))
    assert d2["characters"][0]["namespace"] == "X" and d2["characters"][0]["sequence"] == [] and d2["export"]["category"] == "Cutscene"


def test_broken():
    with pytest.raises(S.ShotError):
        S.loads("{nope")
    with pytest.raises(S.ShotError):
        S.loads('{"format": "x"}')
    with pytest.raises(S.ShotError):
        S.load("no/such.json")


@pytest.mark.parametrize(
    "mutate, path",
    [
        (lambda d: d.update(shot="open"), "shot"),
        (lambda d: d.update(frameRate=24), "frameRate"),
        (lambda d: d.update(range=[10, 5]), "range"),
        (lambda d: d.update(range=[0]), "range"),
        (lambda d: d["cameras"][0].update(end=-5), "cameras[0]"),
        (lambda d: d["cameras"][0].update(node=""), "cameras[0]"),
        (lambda d: d["characters"][0].update(modelId="misoten"), "characters[0]"),
        (lambda d: d["characters"][0].update(sequence="x"), "characters[0].sequence"),
        (lambda d: d["events"][0].update(key="bad key"), "events[0]"),
    ],
)
def test_validation_errors(mutate, path):
    d = make()
    mutate(d)
    assert any(i.level == "error" and i.path == path for i in S.validate(d)), S.validate(d)


def test_warnings():
    d = make()
    d["cameras"][1]["start"] = 100  # 重なる
    d["events"].append({"frame": 999, "key": "Late"})
    d["cameras"][0]["end"] = 300
    ws = [i for i in S.validate(d) if i.level == "warning"]
    assert any("重なって" in i.message for i in ws) and any("の外です" in i.message for i in ws) and any("はみ出" in i.message for i in ws)
    assert S.errors(d) == []


def test_helpers():
    d = make()
    assert S.camera_at(d, 10) == "CAM_Main" and S.camera_at(d, 121) == "CAM_Close" and S.camera_at(d, 500) is None
    S.add_character(d, "MisotenChara")
    S.add_character(d, "Hero")
    S.add_character(d, "MisotenChara")
    assert [f for _, f in S.character_files(d)] == ["Opening01__MisotenChara", "Opening01__MisotenChara_2", "Opening01__Hero", "Opening01__MisotenChara_3"]
    with pytest.raises(S.ShotError):
        S.add_event(d, 999, "X")
    with pytest.raises(S.ShotError):
        S.add_event(d, 5, "bad key")
    with pytest.raises(S.ShotError):
        S.add_camera(d, "C", 10, 5)
    assert S.remove_camera(d, 0) and not S.remove_camera(d, 9)


def test_schema_file_matches():
    schema = json.loads((Path(S.__file__).parent / "shot_schema.json").read_text(encoding="utf-8"))
    d = S.new_shot("A")
    assert set(schema["required"]) <= set(d) and set(schema["properties"]) >= set(d)
