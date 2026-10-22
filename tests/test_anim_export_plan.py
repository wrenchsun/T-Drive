"""分割出力の計画（docs/20 §6。Maya 非依存）。"""

from pathlib import Path

import pytest

from tdrive_anim.core import animset as A
from tdrive_anim.core import export_plan as E


def test_file_names_never_use_at_sign():
    assert E.model_file("MisotenChara") == "SK_MisotenChara.fbx"
    assert E.clip_file("A_MisotenChara_Run_F") == "A_MisotenChara_Run_F.fbx"
    with pytest.raises(E.ExportPlanError):
        E.clip_file("MisotenChara@Run")


def test_default_dirs(tmp_path):
    d = A.new_animset("MisotenChara")
    model, anim = E.resolve_dirs(d, "default", {}, tmp_path)
    assert model == tmp_path / "build" / "unity" / "MisotenChara" and anim == model / "anim"


def test_presets_need_configured_paths(tmp_path):
    d = A.new_animset("MisotenChara")
    d["export"]["category"] = "Player"
    cfg = {"animMs2026Root": "C:/ms", "animDdriveRoot": "C:/dd"}
    m, a = E.resolve_dirs(d, "ms2026", cfg, tmp_path)
    assert m == Path("C:/ms/Assets/_Project/DDrive/SourceAssets/Model/Player") and a == Path("C:/ms/Assets/_Project/DDrive/SourceAssets/Anim/Player")
    m, a = E.resolve_dirs(d, "ddrive", cfg, tmp_path)
    assert m == Path("C:/dd/Assets/SourceAssets/Model/Player") and a == Path("C:/dd/Assets/SourceAssets/Anim/Player")
    with pytest.raises(E.ExportPlanError):
        E.resolve_dirs(d, "ms2026", {}, tmp_path)
    with pytest.raises(E.ExportPlanError):
        E.resolve_dirs(d, "custom", {}, tmp_path)
    d["export"].update(modelDir="X:/m", animDir="X:/a")
    assert E.resolve_dirs(d, "custom", {}, tmp_path) == (Path("X:/m"), Path("X:/a"))
    with pytest.raises(E.ExportPlanError):
        E.resolve_dirs(d, "nope", {}, tmp_path)
    avail = E.available_presets(A.new_animset("X"), {})
    assert avail["default"] == "" and avail["ms2026"] != "" and avail["custom"] != ""


def test_changed_clips(tmp_path):
    anim = tmp_path / "anim"
    anim.mkdir()
    state = {"clips": {}}
    fps = {"A_X_Idle": "aaa", "A_X_Run": "bbb"}
    assert E.changed_clips(["A_X_Idle", "A_X_Run"], fps, state, anim) == ["A_X_Idle", "A_X_Run"]  # 記録なし
    for n in fps:
        (anim / E.clip_file(n)).write_bytes(b"x")
        E.record(state, n, fps[n], anim)
    assert E.changed_clips(list(fps), fps, state, anim) == []  # 変更なし
    assert E.changed_clips(list(fps), {**fps, "A_X_Run": "ccc"}, state, anim) == ["A_X_Run"]  # 指紋が違う
    (anim / E.clip_file("A_X_Idle")).unlink()
    assert E.changed_clips(list(fps), fps, state, anim) == ["A_X_Idle"]  # ファイルが消えた
    assert E.changed_clips(list(fps), fps, state, tmp_path / "other") == list(fps)  # 出力先が変わった


def test_state_roundtrip_and_broken(tmp_path):
    p = E.state_path(tmp_path / "anim" / "X" / "animset.json")
    assert p.name == ".export-state.json"
    s = {"clips": {}}
    E.record(s, "A_X_Idle", "abc", tmp_path)
    E.save_state(p, s)
    assert E.load_state(p) == s
    p.write_text("{broken", encoding="utf-8")
    assert E.load_state(p) == {"clips": {}}
    assert E.load_state(tmp_path / "none.json") == {"clips": {}}
