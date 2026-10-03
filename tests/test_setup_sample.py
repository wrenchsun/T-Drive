"""tools/setup_sample_shizuku.py の Maya 非依存部分（S-1）。"""

import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "setup_sample_shizuku.py"
spec = importlib.util.spec_from_file_location("setup_sample_shizuku", SCRIPT)
sut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sut)


def _touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def source(tmp_path):
    s = tmp_path / "kuromaru9"
    _touch(s / "shizuku/FBX/shizuku.fbx")
    _touch(s / "shizuku/FBX/shizuku_customBody.fbx")
    _touch(s / "shizuku/FBX/shizuku.fbx.meta")
    for n in ("body01_d", "body01_m", "body01_n", "hair01_d", "watchLCD01"):
        _touch(s / f"shizuku/Textures/{n}.png")
        _touch(s / f"shizuku/Textures/{n}.png.meta")
    _touch(s / "shizuku/Animations/Base/anim_idle.fbx")
    _touch(s / "shizuku/Animations/Base/anim_idle.fbx.meta")
    _touch(s / "shizuku/Animations/Base/BlendTrees/x.asset")
    _touch(s / "shizuku/Animations/Gesture/handPose01.fbx")
    _touch(s / "shizuku/Animations/FX/face_default.anim")
    _touch(s / "shizuku/Animations/FX/face_default.anim.meta")
    _touch(s / "shizuku/Materials/body01.mat")
    _touch(s / "shizuku/Prefabs/shizuku.prefab")
    return s


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("TDRIVE_PROJECT", raising=False)
    a = sut.parse_args([])
    assert a.source == tmp_path / "Downloads" / "kuromaru9"
    assert a.project == sut.REPO and a.force is False
    monkeypatch.setenv("TDRIVE_PROJECT", str(tmp_path / "proj"))
    assert sut.parse_args([]).project == tmp_path / "proj"
    b = sut.parse_args(["--source", "s", "--project", "p", "--force"])
    assert b.source == Path("s") and b.project == Path("p") and b.force


def test_validate_ok(source):
    assert sut.validate_source(source) == []


def test_validate_missing_folder(tmp_path):
    errs = sut.validate_source(tmp_path / "nothing")
    assert len(errs) == 1 and "見つかりません" in errs[0]


def test_validate_missing_parts(tmp_path):
    s = tmp_path / "k"
    (s / "shizuku").mkdir(parents=True)
    errs = sut.validate_source(s)
    assert len(errs) == 4


def test_plan_copy(source, tmp_path):
    dest = tmp_path / "proj" / "assets" / "shizuku"
    plan = {(src.relative_to(source).as_posix(), dst.relative_to(dest).as_posix()) for src, dst in sut.plan_copy(source, dest)}
    assert ("shizuku/FBX/shizuku.fbx", "model/shizuku.fbx") in plan
    assert ("shizuku/Textures/body01_d.png", "textures/body01_d.png") in plan
    assert ("shizuku/Animations/Base/anim_idle.fbx", "animations/anim_idle.fbx") in plan
    assert ("shizuku/Animations/Gesture/handPose01.fbx", "animations/handPose01.fbx") in plan
    assert ("shizuku/Animations/FX/face_default.anim", "facial_anims/face_default.anim") in plan
    assert all(".meta" not in s for s, _ in plan)
    assert not any("Materials" in s or "Prefabs" in s or "customBody" in s for s, _ in plan)
    assert len(plan) == 1 + 5 + 2 + 1


def test_execute_copy_idempotent(source, tmp_path):
    dest = tmp_path / "proj" / "assets" / "shizuku"
    plan = sut.plan_copy(source, dest)
    copied, skipped = sut.execute_copy(plan)
    assert len(copied) == len(plan) and not skipped
    copied, skipped = sut.execute_copy(plan)
    assert not copied and len(skipped) == len(plan)
    copied, skipped = sut.execute_copy(plan, force=True)
    assert len(copied) == len(plan)
    # 元が変わったら写し直す
    src, dst = plan[0]
    src.write_bytes(b"changed!")
    copied, _ = sut.execute_copy(plan)
    assert copied == [dst] and dst.read_bytes() == b"changed!"
    # 書き込みは先の下だけ
    assert {p.name for p in (tmp_path / "proj").iterdir()} == {"assets"}


def test_map_materials():
    m = sut.map_materials(["body01", "faceOption", "hair01"], ["body01_d.png", "body01_m.png", "hair01_d.png"])
    assert sut.map_materials(["mat_faceOption1", "mat_hair01", "mat_wear02", "lambert2"], ["faceOption_d.png", "hair01_d.png", "wear02_d.png"]) == {"mat_faceOption1": "faceOption_d.png", "mat_hair01": "hair01_d.png", "mat_wear02": "wear02_d.png", "lambert2": None}
    assert m == {"body01": "body01_d.png", "faceOption": None, "hair01": "hair01_d.png"}


def test_texture_scene_path():
    assert sut.texture_scene_path("body01_d.png") == "$TDRIVE_PROJECT/assets/shizuku/textures/body01_d.png"


def test_no_maya_import_at_module_level():
    assert "maya" not in {k.split(".")[0] for k in vars(sut) if k == "maya"}
