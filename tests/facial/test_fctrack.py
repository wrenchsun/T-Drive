""".fctrack（Timeline 用の演出カーブ）のデータ形式（core/fctrack.py。Maya 非依存）。"""

import json

import pytest

from tdrive_facial.core import fctrack as ft


def _track() -> ft.FacialTrack:
    return ft.FacialTrack(
        shot="S010",
        model="shizuku",
        frame_rate=30,
        range=(0, 240),
        curves={
            "alpha": [(0.0, 1.0), (2.5, 0.0)],
            "emotion.Joy": [(1.0, 0.0), (1.5, 1.0)],
            "useManual": [(0.0, 0.0), (4.0, 1.0)],
            "manualYaw": [(4.0, -30.0), (6.0, 30.0)],
        },
    )


def test_round_trip_dict_and_text():
    t = _track()
    d = ft.to_dict(t)
    assert d["format"] == "FacialTrack" and d["version"] == 1
    assert d["frameRate"] == 30 and d["range"] == [0, 240]
    assert d["curves"]["emotion.Joy"] == [[1.0, 0.0], [1.5, 1.0]]
    back = ft.loads(ft.dumps(t))
    assert back.shot == "S010" and back.model == "shizuku"
    assert back.frame_rate == 30.0 and back.range == (0.0, 240.0)
    assert back.curves == {k: [(a, b) for a, b in v] for k, v in t.curves.items()}
    assert ft.to_dict(back) == d
    assert back.duration_seconds == 8.0


def test_dumps_is_valid_json_one_line_per_curve():
    text = ft.dumps(_track())
    json.loads(text)
    assert text.endswith("}\n")
    assert sum(1 for l in text.splitlines() if l.strip().startswith('"emotion.Joy"')) == 1
    empty = ft.FacialTrack(shot="a", model="b")
    assert json.loads(ft.dumps(empty))["curves"] == {}


def test_save_load_file(tmp_path):
    p = tmp_path / "x" / ft.file_name("S010", "shizuku")
    assert p.name == "S010__shizuku.fctrack"
    ft.save(_track(), p)
    assert ft.to_dict(ft.load(p)) == ft.to_dict(_track())
    assert b"\r\n" not in p.read_bytes() and not p.read_bytes().startswith(b"\xef\xbb\xbf")


def test_unknown_keys_are_kept():
    d = ft.to_dict(_track())
    d["futureKey"] = {"a": 1}
    t = ft.from_dict(d)
    assert t.extra == {"futureKey": {"a": 1}}
    assert ft.to_dict(t)["futureKey"] == {"a": 1}


@pytest.mark.parametrize(
    "mutate, text",
    [
        (lambda d: d.update(format="FacialPose"), "format"),
        (lambda d: d.update(version=2), "version"),
        (lambda d: d.update(version="1"), "version"),
        (lambda d: d.update(frameRate=0), "frameRate"),
        (lambda d: d.update(range=[10, 0]), "range"),
        (lambda d: d.update(range=[0]), "range"),
        (lambda d: d.update(shot=""), "shot"),
        (lambda d: d["curves"].update(bogus=[[0, 1]]), "カーブ名"),
        (lambda d: d["curves"].update({"emotion.": [[0, 1]]}), "カーブ名"),
        (lambda d: d["curves"].update(alpha=[[1.0, 1.0], [0.5, 0.0]]), "昇順"),
        (lambda d: d["curves"].update(alpha=[[0.0, "x"]]), "数"),
        (lambda d: d["curves"].update(alpha=[[0.0, float("nan")]]), "数"),
        (lambda d: d["curves"].update(alpha=[[0.0]]), "数"),
        (lambda d: d["curves"].update(alpha=5), "配列"),
    ],
)
def test_validation_rejects(mutate, text):
    d = ft.to_dict(_track())
    mutate(d)
    with pytest.raises(ft.FcTrackError) as e:
        ft.from_dict(d)
    assert text in str(e.value)


def test_equal_times_allowed_for_steps():
    t = _track()
    t.curves["alpha"] = [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    assert ft.validate(t) == []


def test_not_json_and_not_object():
    with pytest.raises(ft.FcTrackError):
        ft.loads("{not json")
    with pytest.raises(ft.FcTrackError):
        ft.loads("[1, 2]")


def test_validate_returns_list_and_save_refuses_invalid(tmp_path):
    t = _track()
    t.frame_rate = -1
    assert ft.validate(t)
    with pytest.raises(ft.FcTrackError):
        ft.save(t, tmp_path / "a.fctrack")
    assert not (tmp_path / "a.fctrack").exists()


def test_curve_names():
    assert ft.emotion_curve_name("Joy") == "emotion.Joy"
    assert all(ft.is_valid_curve_name(n) for n in ("alpha", "useManual", "manualYaw", "manualPitch", "emotion.Joy"))
    assert not ft.is_valid_curve_name("enable")


def test_loads_rejects_non_finite_and_deep_json():
    """C-4: .fctrack の読み込みも NaN / Infinity / 深すぎる入れ子を拒否する（C# の MiniJson と同じ）。"""
    import pytest

    from tdrive_facial.core import fctrack

    for bad in ('{"curves": {"a": [[0, NaN]]}}', '{"curves": {"a": [[0, 1e999]]}}', '{"x": ' + "[" * 300 + "]" * 300 + "}"):
        with pytest.raises(fctrack.FcTrackError):
            fctrack.loads(bad)


def test_exaggeration_curve_round_trip_and_optional():
    assert "exaggeration" in ft.FIXED_CURVES and ft.is_valid_curve_name("exaggeration")
    without = _track()
    assert "exaggeration" not in ft.loads(ft.dumps(without)).curves  # 無いファイルも読める（従来どおり）
    with_ex = _track()
    with_ex.curves["exaggeration"] = [(0.0, 1.0), (1.0, 0.25), (1.0, 0.0)]
    back = ft.loads(ft.dumps(with_ex))
    assert back.curves["exaggeration"] == [(0.0, 1.0), (1.0, 0.25), (1.0, 0.0)]
    assert back.version == 1 and ft.to_dict(back) == ft.to_dict(with_ex)
    d = ft.to_dict(with_ex)
    d["curves"]["exaggeration"] = [[1.0, 0.0], [0.5, 1.0]]
    with pytest.raises(ft.FcTrackError, match="昇順"):
        ft.from_dict(d)
