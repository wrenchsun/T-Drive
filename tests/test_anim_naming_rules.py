"""命名規則の検査・提案（docs/20 §2。Maya 非依存）。"""

import json

import pytest

from tdrive_anim.core import look_follow
from tdrive_anim.core import naming_rules as N

M = "MisotenChara"


def sug(kind, name):
    f = N.inspect(kind, name, M)
    return f.suggestion if f else None


@pytest.mark.parametrize(
    "kind, name, expected",
    [
        (N.MESH, "MisotenChara_Rig5_IdolGirl_Body_Chest", "SK_MisotenChara_Body"),
        (N.MESH, "MisotenChara_Rig5_IdolGirl_Skirt", "SK_MisotenChara_Skirt"),
        (N.MESH, "MisotenChara_Rig5_IdolGirl_Ribbonback", "SK_MisotenChara_RibbonBack"),
        (N.MATERIAL, "Hair", "M_MisotenChara_Hair"),
        (N.MATERIAL, "Face", "M_MisotenChara_Face"),
        (N.MATERIAL, "IdolGirl_Other2", "M_MisotenChara_Other2"),
        (N.MATERIAL, "MisotenChara_Rig5_Hair", "M_MisotenChara_Hair"),
        (N.CLIP, "Run_F", "A_MisotenChara_Run_F"),
        (N.CLIP, "A_MisotenChara_Run@Anim", "A_MisotenChara_Run"),
        (N.CLIP, "MisotenChara@Jump", "A_MisotenChara_Jump"),
    ],
)
def test_suggestions(kind, name, expected):
    assert sug(kind, name) == expected


@pytest.mark.parametrize(
    "kind, name",
    [
        (N.MESH, "SK_MisotenChara_Body"),
        (N.MESH, "SM_MisotenChara_Prop"),
        (N.MATERIAL, "M_MisotenChara_Hair"),
        (N.MATERIAL, "MisotenChara_Rig5_Hair_tdToon"),  # プレビュー用は対象外
        (N.TEXTURE, "T_MisotenChara_Face_BaseColor.png"),
        (N.CLIP, "A_MisotenChara_Idle"),
        (N.CLIP, "A_MisotenChara_Run_F"),
        (N.CLIP, "A_MisotenChara_Pose01"),
        (N.CLIP, "A_MisotenChara_Jump"),
        (N.SHOT, "Opening01"),
        (N.PROP, "PRP_Mic"),
        (N.EVENT, "EVT_Hit"),
    ],
)
def test_valid_names(kind, name):
    assert N.inspect(kind, name, M) is None


def test_namespace_is_stripped():
    assert N.inspect(N.MESH, "MisotenChara:SK_MisotenChara_Body", M) is None


@pytest.mark.parametrize(
    "name, text",
    [
        ("A_MisotenChara_Run@Anim", "@"),
        ("Cube (1)", "スペース"),
        ("頭", "日本語"),
        ("M_MisotenChara_Hair#2", "#"),
        ("test", "捨て名"),
        ("pCube1", "自動名"),
        ("polySurface12", "自動名"),
        ("A_MisotenChara_Idle_v2", "版"),
        ("1stMesh", "数字"),
    ],
)
def test_forbidden_is_error(name, text):
    f = N.inspect(N.MESH, name, M)
    assert f is not None and f.severity == N.ERROR and text in f.reason, f


def test_prefix_mismatch_is_warning_but_clip_form_is_error():
    assert N.inspect(N.MESH, "Body", M).severity == N.WARNING
    assert N.inspect(N.MATERIAL, "Hair", M).severity == N.WARNING
    assert N.inspect(N.CLIP, "Run", M).severity == N.ERROR


def test_out_of_vocabulary_is_warning():
    f = N.inspect(N.CLIP, "A_MisotenChara_Dance", M)
    assert f.severity == N.WARNING and "語彙" in f.reason


def test_texture_proposal_only():
    f = N.inspect(N.TEXTURE, "d3adbeef0123456789abcdef01234567.png", M)
    assert f.proposal_only and f.suggestion.startswith("T_MisotenChara_Part_BaseColor") and f.suggestion.endswith(".png")
    n = N.inspect(N.TEXTURE, "face_normal.png", M)
    assert n.suggestion == "T_MisotenChara_Face_Normal.png"


def test_shot_rules():
    assert N.inspect(N.SHOT, "opening01", M).severity == N.ERROR
    assert N.inspect(N.SHOT, "Open__ing", M).severity == N.ERROR


def test_cameras():
    assert N.inspect_cameras(["persp1"]) == []
    got = N.inspect_cameras(["Main", "CAM_Close"])
    assert [f.name for f in got] == ["Main"] and got[0].suggestion == "CAM_Main"


def test_unique_suggestions():
    fs = [N.inspect(N.MESH, "MisotenChara_Rig5_IdolGirl_Body_A", M), N.inspect(N.MESH, "MisotenChara_Rig5_IdolGirl_Body_B", M)]
    N.unique_suggestions(fs, taken={"SK_MisotenChara_Hat"})
    assert [f.suggestion for f in fs] == ["SK_MisotenChara_Body", "SK_MisotenChara_Body2"]


def test_profile_override_and_broken(tmp_path):
    (tmp_path / "_naming.json").write_text(json.dumps({"meshPrefix": "SKM", "vocabulary": ["Dance"], "partWords": {"hair": "Kami"}}), encoding="utf-8")
    (tmp_path / "_vocabulary.json").write_text(json.dumps(["Sleep"]), encoding="utf-8")
    p, warns = N.load_profile(tmp_path / "_naming.json", tmp_path / "_vocabulary.json")
    assert warns == [] and p.mesh_prefix == "SKM" and "Dance" in p.vocabulary and "Sleep" in p.vocabulary
    assert N.suggest_mesh("Hair", M, p) == "SKM_MisotenChara_Kami"
    (tmp_path / "_naming.json").write_text("{bad", encoding="utf-8")
    p2, warns2 = N.load_profile(tmp_path / "_naming.json")
    assert p2.mesh_prefix == "SK" and len(warns2) == 1
    p3, warns3 = N.load_profile(tmp_path / "nothing.json")
    assert warns3 == [] and p3.mesh_prefix == "SK"


def test_look_follow():
    look = {
        "materials": {"Hair": {"common": {}}, "Face": {}},
        "parts": {"hair": {"materials": ["Hair"], "role": "hair"}},
        "variants": {"B": {"overrides": {"Hair": {"x": 1}}}},
        "characterSettings": {"viewCorrection": {"mesh": "Head", "front": ""}},
    }
    assert look_follow.rename_materials(look, {"Hair": "M_X_Hair", "Nope": "M_X_Nope"}) == 3
    assert "M_X_Hair" in look["materials"] and "Hair" not in look["materials"]
    assert look["parts"]["hair"]["materials"] == ["M_X_Hair"]
    assert "M_X_Hair" in look["variants"]["B"]["overrides"]
    # 既にある名前へは上書きしない
    assert look_follow.rename_materials(look, {"Face": "M_X_Hair"}) == 0
    assert look_follow.rename_meshes(look, {"Head": "SK_X_Head"}) == 1 and look["characterSettings"]["viewCorrection"]["mesh"] == "SK_X_Head"


def test_texture_problems_never_block_output():
    f = N.inspect(N.TEXTURE, "94fa0d80-6b72-4339-99fc-10a860321485.png", M)
    assert f.severity == N.WARNING and f.proposal_only


# ---------------------------------------------------------------- docs/21 TS-15: 大文字小文字・ModelId の境界・自動名の誤検出
@pytest.mark.parametrize(
    "kind, name, expected",
    [
        (N.MESH, "sk_MisotenChara_Body", "SK_MisotenChara_Body"),
        (N.MESH, "SK_misotenchara_Body", "SK_MisotenChara_Body"),
        (N.MESH, "SK_MisotenChara_body", "SK_MisotenChara_Body"),
        (N.MATERIAL, "m_MisotenChara_hair", "M_MisotenChara_Hair"),
        (N.CLIP, "a_MisotenChara_idle", "A_MisotenChara_Idle"),
        (N.CLIP, "A_MisotenChara_idle", "A_MisotenChara_Idle"),
        (N.CLIP, "A_MISOTENCHARA_Run_F", "A_MisotenChara_Run_F"),
    ],
)
def test_case_mistakes_get_a_clean_suggestion(kind, name, expected):
    f = N.inspect(kind, name, M)
    assert f is not None and f.suggestion == expected, f


def test_model_id_with_underscore_is_not_a_valid_model_id():
    from tdrive_anim.core import animset as A

    assert A.MODEL_ID_PATTERN.match("MisotenChara") and not A.MODEL_ID_PATTERN.match("Misoten_Chara")
    # 検査は ModelId の形を前提にしない（ModelId 側で止める）。`_` の入った ModelId で作った名前は、規則の形（SK_<ModelId>_<部位>）に合わない
    assert N.inspect(N.MESH, "SK_Misoten_Chara_Body", "MisotenChara") is not None


def test_mgear_joint_names_are_not_mistaken_for_maya_auto_names():
    from pathlib import Path

    names = [l.strip() for l in (Path(__file__).parent / "fixtures" / "anim" / "misoten_joint_names.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(names) == 141
    assert [n for n in names if N._AUTO_NAME.match(n)] == []  # `joint1` のような自動名と間違えない
    assert N.common_problems("spine_C0_0_jnt", N.default_profile()) == []
    assert N.common_problems("joint12", N.default_profile())  # 本物の自動名は止まる
