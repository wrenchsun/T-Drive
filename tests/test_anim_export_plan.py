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


def test_shared_preset_needs_configured_folder(tmp_path):
    """出力先は「既定」と「共有フォルダ」の 2 択（AN-F-18）。共有フォルダの下は Model|Anim|Cutscene/<Category>。"""
    d = A.new_animset("MisotenChara")
    d["export"]["category"] = "Player"
    assert E.PRESETS == ("default", "shared")
    cfg = {"animSharedRoot": "G:/Shared/Unity"}
    m, a = E.resolve_dirs(d, "shared", cfg, tmp_path)
    assert m == Path("G:/Shared/Unity/Model/Player") and a == Path("G:/Shared/Unity/Anim/Player")
    assert E.resolve_shot_dir({"export": {"category": "Cutscene"}}, "shared", cfg, tmp_path) == Path("G:/Shared/Unity/Cutscene/Cutscene")
    with pytest.raises(E.ExportPlanError):
        E.resolve_dirs(d, "shared", {}, tmp_path)
    for old in ("ms2026", "ddrive", "custom", "nope"):
        with pytest.raises(E.ExportPlanError):
            E.resolve_dirs(d, old, cfg, tmp_path)
    avail = E.available_presets(A.new_animset("X"), {})
    assert avail["default"] == "" and avail["shared"] != ""


def test_no_ddrive_or_ms2026_presets_left():
    """D-Drive / MS2026 へのパス・ブランチ判定は持たない（デザイナーの PC に D-Drive は無い。AN-F-18）。"""
    for name in ("write_problem", "write_warning", "git_branch", "MS2026_SUB", "DDRIVE_SUB", "CONFIG_KEYS"):
        assert not hasattr(E, name), name


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


def test_shot_dirs(tmp_path):
    from tdrive_anim.core import shot as SH

    sh = SH.new_shot("Opening01")
    sh["export"]["category"] = "Opening"
    assert E.resolve_shot_dir(sh, "default", {}, tmp_path) == tmp_path / "build" / "cutscene" / "Opening"
    cfg = {"animSharedRoot": "G:/Shared"}
    assert E.resolve_shot_dir(sh, "shared", cfg, tmp_path) == Path("G:/Shared/Cutscene/Opening")
    with pytest.raises(E.ExportPlanError):
        E.resolve_shot_dir(sh, "shared", {}, tmp_path)
    with pytest.raises(E.ExportPlanError):
        E.resolve_shot_dir(sh, "custom", {}, tmp_path)


def _git_repo(path, branch):
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-q", "-m", "x"], check=True)
