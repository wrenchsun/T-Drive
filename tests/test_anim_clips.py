"""クリップの操作（docs/20 §3。Maya 非依存）。"""

import pytest

from tdrive_anim.core import animset as A
from tdrive_anim.core import clips as C

M = "MisotenChara"


def make():
    return A.new_animset(M)


def test_compose_name():
    assert C.compose_name(M, "Run", "F") == "A_MisotenChara_Run_F"
    assert C.compose_name(M, "Pose", "", 1) == "A_MisotenChara_Pose01"
    assert C.compose_name(M, "Run", "L", 2) == "A_MisotenChara_Run_L02"
    for bad in ("run", "Run Fast", "", "Ru1"):
        with pytest.raises(A.AnimSetError):
            C.compose_name(M, bad)
    with pytest.raises(A.AnimSetError):
        C.compose_name(M, "Run", "X")


def test_from_range_defaults_and_duplicates():
    d = make()
    idle = C.from_range(d, "Idle", 0, 60)
    assert idle["name"] == "A_MisotenChara_Idle" and idle["loop"] and idle["stateGroup"] == "locomotion"
    jump = C.from_range(d, "Jump", 100, 118)
    assert not jump["loop"] and jump["stateGroup"] == "oneshot"
    with pytest.raises(A.AnimSetError):
        C.from_range(d, "Idle", 0, 5)  # 重複名はエラー
    with pytest.raises(A.AnimSetError):
        C.from_range(d, "Run", 10, 5)
    assert C.frame_count(idle) == 61 and C.duration_sec(idle, 30) == 2.0
    assert A.errors(d) == []


def test_unique_name_and_duplicate():
    d = make()
    C.from_range(d, "Run", 0, 18, "F")
    dup = C.duplicate(d, "A_MisotenChara_Run_F")
    assert dup["name"] == "A_MisotenChara_Run_F02"
    dup2 = C.duplicate(d, "A_MisotenChara_Run_F")
    assert dup2["name"] == "A_MisotenChara_Run_F03"
    assert [c["name"] for c in d["clips"]][:2] == ["A_MisotenChara_Run_F", "A_MisotenChara_Run_F03"]  # 元の直後に入る
    assert A.errors(d) == []
    dup["events"].append({"frame": 1, "key": "x"})
    assert d["clips"][0]["events"] == []  # 深いコピー


def test_update_validates_without_changing():
    d = make()
    C.from_range(d, "Idle", 0, 60)
    C.from_range(d, "Jump", 100, 118)
    C.update(d, "A_MisotenChara_Idle", end=90, loop=False, crossFade=0.08)
    c = A.find_clip(d, "A_MisotenChara_Idle")
    assert c["end"] == 90 and not c["loop"] and c["crossFade"] == 0.08
    before = dict(c)
    for bad in ({"end": -5}, {"name": "A_MisotenChara_Jump"}, {"layer": "Nope"}, {"crossFade": -1}, {"rootMotion": "x"}, {"start": "a"}):
        with pytest.raises(A.AnimSetError):
            C.update(d, "A_MisotenChara_Idle", **bad)
        assert A.find_clip(d, "A_MisotenChara_Idle") == before  # 失敗したら変わらない
    with pytest.raises(A.AnimSetError):
        C.update(d, "nope", loop=True)


def test_events():
    d = make()
    c = C.from_range(d, "Run", 0, 18, "F")
    C.add_foot_plant(c, 13, "right")
    C.add_foot_plant(c, 4, "left")
    assert [(e["frame"], e["key"], e["args"]) for e in c["events"]] == [(4, "OnFootPlant", 0), (13, "OnFootPlant", 1)]
    C.add_event(c, 9, "Hit")
    assert [e["frame"] for e in c["events"]] == [4, 9, 13] and "args" not in c["events"][1]
    for bad in (lambda: C.add_event(c, 99, "x"), lambda: C.add_event(c, 5, "  "), lambda: C.add_foot_plant(c, 5, "middle")):
        with pytest.raises(A.AnimSetError):
            bad()
    assert C.remove_event(c, 1) and not C.remove_event(c, 10)
    assert A.errors(d) == [] and A.validate(d) == []


def test_search():
    d = make()
    C.from_range(d, "Idle", 0, 60)
    run = C.from_range(d, "Run", 0, 18, "F")
    C.add_foot_plant(run, 4, "left")
    assert [c["name"] for c in C.search(d, "run")] == ["A_MisotenChara_Run_F"]
    assert [c["name"] for c in C.search(d, "footplant")] == ["A_MisotenChara_Run_F"]
    assert len(C.search(d, "")) == 2 and C.search(d, "zzz") == []


def test_attr_clipboard():
    d = make()
    a = C.from_range(d, "Run", 0, 18, "F")
    b = C.from_range(d, "Jump", 20, 30)
    a["crossFade"], a["rootMotion"] = 0.08, "bake"
    text = C.attrs_to_text(a)
    attrs = C.attrs_from_text(text)
    assert set(attrs) == set(C.ATTR_KEYS) and "name" not in attrs and "start" not in attrs
    C.update(d, b["name"], **attrs)
    assert b["crossFade"] == 0.08 and b["rootMotion"] == "bake" and b["start"] == 20
    for bad in ("", "{not json", '{"format": "x"}', "[1,2]", "hello"):
        with pytest.raises(A.AnimSetError):
            C.attrs_from_text(bad)
