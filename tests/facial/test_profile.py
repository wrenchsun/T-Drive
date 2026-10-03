"""命名規則プロファイル（core/profile.py）・同梱プリセット・スキーマ（schema/fcprofile.schema.json）。"""

import json
import warnings
from pathlib import Path

import pytest

from tdrive_facial.core import model as m
from tdrive_facial.core import profile as P

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "fcprofile.schema.json"
PRESET_DIR = ROOT / "maya" / "scripts" / "tdrive_facial" / "profiles"
PRESET_NAMES = ["arkit52", "vrchat_viseme", "metahuman", "shizuku"]
SETUP_REPORT = ROOT / "assets" / "shizuku" / "setup_report.json"

# UE 版 FacialNamingProfile.cpp の ARKit プリセットと同じ 52 個（ここに別に書いて突き合わせる）
ARKIT_52 = (
    "eyeBlinkLeft eyeLookDownLeft eyeLookInLeft eyeLookOutLeft eyeLookUpLeft eyeSquintLeft eyeWideLeft "
    "eyeBlinkRight eyeLookDownRight eyeLookInRight eyeLookOutRight eyeLookUpRight eyeSquintRight eyeWideRight "
    "jawForward jawLeft jawRight jawOpen mouthClose mouthFunnel mouthPucker mouthLeft mouthRight "
    "mouthSmileLeft mouthSmileRight mouthFrownLeft mouthFrownRight mouthDimpleLeft mouthDimpleRight "
    "mouthStretchLeft mouthStretchRight mouthRollLower mouthRollUpper mouthShrugLower mouthShrugUpper "
    "mouthPressLeft mouthPressRight mouthLowerDownLeft mouthLowerDownRight mouthUpperUpLeft mouthUpperUpRight "
    "browDownLeft browDownRight browInnerUp browOuterUpLeft browOuterUpRight "
    "cheekPuff cheekSquintLeft cheekSquintRight noseSneerLeft noseSneerRight tongueOut"
).split()
VRC_VISEMES = [f"vrc.v_{s}" for s in "sil aa ch dd e ff ih kk nn oh ou pp rr ss th".split()]


# ---------------------------------------------------------------------------
# スキーマ（jsonschema が無い環境用の最小の検査器 + あれば本物でも確かめる）
# ---------------------------------------------------------------------------


def _type_ok(value, t):
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    }[t](value)


def mini_validate(value, schema, path="$"):
    errors = []
    t = schema.get("type")
    if t is not None and not _type_ok(value, t):
        return [f"{path}: type {t} ではない"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const {schema['const']!r} ではない")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: 短すぎる")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value < schema.get("minimum", float("-inf")):
        errors.append(f"{path}: {value} < minimum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1 << 30):
            errors.append(f"{path}: 要素数が範囲外")
        if "items" in schema:
            for i, v in enumerate(value):
                errors += mini_validate(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errors.append(f"{path}: 必須キー {k} が無い")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errors += mini_validate(v, props[k], f"{path}.{k}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += mini_validate(v, schema["additionalProperties"], f"{path}.{k}")
    return errors


def schema_errors(instance):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = mini_validate(instance, schema)
    try:
        import jsonschema
    except ImportError:
        return errors
    return errors + [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(instance)]


def test_schema_is_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    try:
        import jsonschema
    except ImportError:
        return
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_presets_match_schema(name):
    data = json.loads((PRESET_DIR / f"{name}{P.PROFILE_EXTENSION}").read_text(encoding="utf-8"))
    assert schema_errors(data) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"version": 1, "name": "x"},  # format 無し
        {"format": "FacialCorrection", "version": 1, "name": "x"},
        {"format": "FacialNamingProfile", "version": 0, "name": "x"},
        {"format": "FacialNamingProfile", "version": 1, "name": ""},
        {"format": "FacialNamingProfile", "version": 1, "name": "x", "limits": {"a": [0]}},
        {"format": "FacialNamingProfile", "version": 1, "name": "x", "standardCurves": [1]},
    ],
)
def test_schema_rejects_bad_profiles(bad):
    assert schema_errors(bad)


# ---------------------------------------------------------------------------
# 同梱プリセット
# ---------------------------------------------------------------------------


def test_builtin_profiles_discovers_four_presets():
    profiles = P.builtin_profiles()
    assert sorted(profiles) == sorted(PRESET_NAMES)
    for name, prof in profiles.items():
        assert prof.name == name  # ファイル名の語幹と name が一致


def test_arkit_has_exactly_52_unique_standard_names():
    prof = P.builtin_profiles()["arkit52"]
    assert len(prof.standard_curves) == 52
    assert len(set(prof.standard_curves)) == 52
    assert prof.standard_curves == ARKIT_52
    assert (prof.mirror.suffix_l, prof.mirror.suffix_r) == ("Left", "Right")
    assert set(prof.limits) == set(ARKIT_52)
    assert all(v == (0.0, 1.0) for v in prof.limits.values())


def test_vrchat_visemes():
    prof = P.builtin_profiles()["vrchat_viseme"]
    assert prof.standard_curves == VRC_VISEMES
    assert (prof.mirror.suffix_l, prof.mirror.suffix_r) == ("_L", "_R")
    assert set(prof.limits) == set(VRC_VISEMES)


def test_metahuman_has_no_names_but_a_mirror_rule():
    prof = P.builtin_profiles()["metahuman"]
    assert prof.standard_curves == []
    assert prof.limits == {}
    assert (prof.mirror.suffix_l, prof.mirror.suffix_r) == ("L", "R")


def test_shizuku_names_are_bs_targets():
    prof = P.builtin_profiles()["shizuku"]
    names = prof.standard_curves
    assert len(names) == 75 and len(set(names)) == 75
    assert all(n.startswith("bs.") and not n.startswith("bs.__") for n in names)
    # モデルの綴りのまま
    assert "bs.eye_libUp_L" in names and "bs.eye_lidUp_R" in names and "bs.jaw_forwerd" in names
    assert (prof.mirror.suffix_l, prof.mirror.suffix_r) == ("_L", "_R")
    assert "shizuku" in prof.description


@pytest.mark.skipif(not SETUP_REPORT.is_file(), reason="assets/shizuku/setup_report.json が無い")
def test_shizuku_preset_matches_setup_report():
    report = json.loads(SETUP_REPORT.read_text(encoding="utf-8"))
    expected = ["bs." + t for t in report["face_targets"] if not t.startswith("__")]
    assert sorted(P.builtin_profiles()["shizuku"].standard_curves) == sorted(expected)


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_presets_round_trip_byte_for_byte(name):
    path = PRESET_DIR / f"{name}{P.PROFILE_EXTENSION}"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"}\n")
    prof = P.load(path)
    assert P.dumps(prof).encode("utf-8") == raw


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------


def test_load_defaults_and_unknown_keys_preserved():
    text = json.dumps(
        {
            "format": "FacialNamingProfile",
            "version": 1,
            "name": "x",
            "futureKey": {"a": [1, 2]},
            "mirror": {"suffixL": "Lt", "weird": 3},
            "limits": {"a": [0, 2], "bad": [1], "worse": "no"},
        }
    )
    prof = P.loads(text)
    assert prof.description == "" and prof.standard_curves == []
    assert prof.mirror.suffix_l == "Lt" and prof.mirror.suffix_r == "_R"  # 欠けた R は UE の既定値
    assert prof.limits == {"a": (0.0, 2.0)}
    assert prof.extra == {"futureKey": {"a": [1, 2]}}
    assert prof.mirror.extra == {"weird": 3}
    out = json.loads(P.dumps(prof))
    assert out["futureKey"] == {"a": [1, 2]} and out["mirror"]["weird"] == 3
    assert out["limits"] == {"a": [0, 2]}


def test_dumps_is_deterministic_and_key_order_fixed():
    prof = P.NamingProfile(name="p", standard_curves=["b", "a"], limits={"b": (0.0, 1.5), "a": (-1.0, 1.0)})
    s = P.dumps(prof)
    assert s == P.dumps(P.loads(s))
    assert list(json.loads(s)) == ["format", "version", "name", "description", "standardCurves", "mirror", "limits"]
    assert '"b": [0, 1.5]' in s and '"a": [-1, 1]' in s
    assert s.endswith("}\n")


def test_save_writes_utf8_lf_no_bom(tmp_path):
    prof = P.NamingProfile(name="日本語", description="説明\n二行目")
    path = tmp_path / "sub" / "p.fcprofile.json"
    P.save(prof, path)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    assert "日本語".encode("utf-8") in raw  # ensure_ascii にしない
    assert P.load(path) == prof


def test_load_accepts_bom_and_rejects_wrong_format():
    ok = '{"format": "FacialNamingProfile", "version": 1, "name": "x"}'
    assert P.loads("﻿" + ok).name == "x"
    with pytest.raises(P.ProfileError):
        P.loads('{"format": "FacialCorrection", "version": 1}')
    with pytest.raises(P.ProfileError):
        P.loads("[1, 2]")
    with pytest.raises(P.ProfileError):
        P.loads("{not json")


def test_newer_version_warns_but_loads():
    with pytest.warns(P.ProfileVersionWarning):
        prof = P.loads('{"format": "FacialNamingProfile", "version": 99, "name": "x"}')
    assert prof.version == 99


def test_nan_cannot_be_written():
    with pytest.raises(ValueError):
        P.dumps(P.NamingProfile(name="x", limits={"a": (0.0, float("nan"))}))


# ---------------------------------------------------------------------------
# find_profile
# ---------------------------------------------------------------------------


def test_find_profile_builtin_and_missing():
    assert P.find_profile("shizuku").name == "shizuku"
    assert P.find_profile("no_such_profile") is None
    assert P.find_profile("") is None


def test_find_profile_project_dir_wins_over_builtin(tmp_path):
    d = P.project_profiles_dir(tmp_path, "shizuku")
    assert d == tmp_path / "facial" / "shizuku" / "profiles"
    mine = P.NamingProfile(name="shizuku", description="プロジェクト版", standard_curves=["bs.only"])
    P.save(mine, d / "shizuku.fcprofile.json")
    found = P.find_profile("shizuku", [d])
    assert found.description == "プロジェクト版"
    assert P.find_profile("arkit52", [d]).name == "arkit52"  # 無ければ同梱へ


def test_find_profile_by_inner_name_and_skips_broken_files(tmp_path):
    (tmp_path / "broken.fcprofile.json").write_text("{oops", encoding="utf-8")
    P.save(P.NamingProfile(name="inner"), tmp_path / "other_filename.fcprofile.json")
    assert P.find_profile("inner", [tmp_path]).name == "inner"
    assert P.find_profile("broken", [tmp_path]) is None


# ---------------------------------------------------------------------------
# apply_to_document
# ---------------------------------------------------------------------------


def _profile():
    return P.NamingProfile(
        name="prof",
        mirror=P.MirrorRule("Left", "Right", ["jaw"]),
        limits={"a": (0.0, 1.0), "b": (0.0, 2.0)},
    )


def test_apply_copies_mirror_and_limits_and_sets_profile_name():
    doc = m.Document()
    doc.mirror.bone_axis = "X"
    doc.mirror.enabled = False
    res = P.apply_to_document(_profile(), doc)
    assert doc.profile == "prof"
    assert (doc.mirror.suffix_l, doc.mirror.suffix_r, doc.mirror.exclude) == ("Left", "Right", ["jaw"])
    assert doc.mirror.bone_axis == "X" and doc.mirror.enabled is False  # プロファイルの持ち物ではない
    assert doc.limits == {"a": (0.0, 1.0), "b": (0.0, 2.0)}
    assert res.limits_added == 2 and res.limits_kept == 0
    doc.mirror.exclude.append("x")
    assert _profile().mirror.exclude == ["jaw"]  # 共有しない


def test_apply_keeps_per_character_limits_unless_overwrite():
    doc = m.Document(limits={"a": (0.0, 1.5), "mine": (0.0, 3.0)})
    res = P.apply_to_document(_profile(), doc)
    assert doc.limits == {"a": (0.0, 1.5), "mine": (0.0, 3.0), "b": (0.0, 2.0)}
    assert (res.limits_added, res.limits_kept) == (1, 1)

    res = P.apply_to_document(_profile(), doc, overwrite=True)
    assert doc.limits == {"a": (0.0, 1.0), "mine": (0.0, 3.0), "b": (0.0, 2.0)}  # 自分だけの項目は残る
    assert res.limits_overwritten == 1


def test_apply_without_profile_limits_keeps_limits_none():
    doc = m.Document()
    P.apply_to_document(P.NamingProfile(name="empty"), doc)
    assert doc.limits is None and doc.profile == "empty"


def test_apply_preset_to_document_then_serialises(tmp_path):
    from tdrive_facial.core import fcpose_io as io

    doc = m.Document()
    P.apply_to_document(P.builtin_profiles()["arkit52"], doc)
    again = io.loads(io.dumps(doc))
    assert again.profile == "arkit52" and again.mirror.suffix_l == "Left" and len(again.limits) == 52


# ---------------------------------------------------------------------------
# 可動域・clamp
# ---------------------------------------------------------------------------


def test_effective_limit_order_doc_then_profile_then_default():
    doc = m.Document(limits={"a": (0.0, 2.0)})
    prof = P.NamingProfile(name="p", limits={"a": (0.0, 1.0), "b": (-1.0, 1.0)})
    assert P.effective_limit(doc, prof, "a") == (0.0, 2.0)
    assert P.effective_limit(doc, prof, "b") == (-1.0, 1.0)
    assert P.effective_limit(doc, prof, "zzz") == (0.0, 1.0)
    assert P.effective_limit(None, None, "a") == (0.0, 1.0)
    assert P.has_limit(doc, prof, "b") and not P.has_limit(doc, prof, "zzz")


def test_clamp_pose_default_range_and_exaggeration():
    doc = m.Document(limits={"ex": (0.0, 2.0), "neg": (-1.0, 1.0)})
    pose = m.SourcePose(
        curves={"plain": 1.7, "low": -0.3, "ex": 1.7, "ex_over": 3.0, "neg": -0.5, "nan": float("nan")},
        bones={"b": m.BoneOffset(t=(1.0, 2.0, 3.0))},
    )
    doc.limits["ex_over"] = (0.0, 2.0)
    out = P.clamp_pose(pose, doc, None)
    assert out.curves["plain"] == 1.0  # 可動域が無ければ 0〜1
    assert out.curves["low"] == 0.0
    assert out.curves["ex"] == 1.7  # limits が許せば 1 を超えてよい
    assert out.curves["ex_over"] == 2.0  # 最大 2
    assert out.curves["neg"] == -0.5
    assert out.curves["nan"] == 0.0
    assert out.bones["b"] == pose.bones["b"] and out.bones["b"] is not pose.bones["b"]
    assert pose.curves["plain"] == 1.7  # 元は変えない


def test_clamp_pose_uses_profile_limits_when_doc_has_none():
    prof = P.NamingProfile(name="p", limits={"a": (0.0, 2.0)})
    out = P.clamp_pose(m.SourcePose(curves={"a": 1.5, "b": 1.5}), m.Document(), prof)
    assert out.curves == {"a": 1.5, "b": 1.0}


def test_missing_standard_curves_is_case_sensitive_and_ordered():
    prof = P.NamingProfile(name="p", standard_curves=["bs.a", "bs.B", "bs.c"])
    assert P.missing_standard_curves(prof, ["bs.c", "bs.b", "bs.a"]) == ["bs.B"]
    assert P.missing_standard_curves(prof, []) == ["bs.a", "bs.B", "bs.c"]


def test_missing_standard_curves_node_prefix_rule():
    """ノード名つき（`bs.jawOpen`）は完全一致、ノード名なし（`jawOpen`）はどのノードのターゲットにも一致する（Setup / 検証 / シェイプタブ共通）。"""
    prof = P.NamingProfile(name="p", standard_curves=["jawOpen", "bs.mouthSmile", "other.eyeBlink", "missingOne"])
    have = ["bs.jawOpen", "bs.mouthSmile", "bs.eyeBlink"]
    assert P.missing_standard_curves(prof, have) == ["other.eyeBlink", "missingOne"]
    assert P.missing_standard_curves(prof, ["blendShape1.jawOpen"]) == ["bs.mouthSmile", "other.eyeBlink", "missingOne"]
    assert P.curve_matches("bs.jawOpen", ["blendShape1.jawOpen"]) is False
    assert P.curve_matches("jawopen", ["bs.jawOpen"]) is False
    assert P.curve_matches("jawOpen", ["jawOpen"]) is True  # ターゲット名だけが渡されても、ノード名なしの項目には一致する


# ---------------------------------------------------------------------------
# ミラーの名前（UE 版と同じ規則）
# ---------------------------------------------------------------------------


def test_mirror_name_and_exclusion():
    assert P.mirror_name("bs.smile_L", "_L", "_R") == "bs.smile_R"
    assert P.mirror_name("bs.smile_R", "_L", "_R") == "bs.smile_L"
    assert P.mirror_name("bs.jaw_open", "_L", "_R") == "bs.jaw_open"
    assert P.mirror_name("eyeBlinkLeft", "Left", "Right") == "eyeBlinkRight"
    assert P.mirror_name("x_L", "", "_R") == "x_L"
    assert P.mirror_name("x_l", "_L", "_R") == "x_l"  # 大文字小文字を区別
    assert P.is_mirror_excluded("bs.mouth_left", ["mouth"])
    assert not P.is_mirror_excluded("bs.eye", ["mouth", ""])


# ---------------------------------------------------------------------------
# Maya 非依存
# ---------------------------------------------------------------------------


def test_new_modules_do_not_import_maya():
    core = ROOT / "maya" / "scripts" / "tdrive_facial" / "core"
    for f in (core / "profile.py", core / "validate.py"):
        for line in f.read_text(encoding="utf-8").splitlines():
            assert not line.strip().startswith(("import maya", "from maya")), f"{f.name}: {line}"
    import ast

    for f in (core / "profile.py", core / "validate.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("maya") for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("maya")


def test_warnings_not_raised_for_normal_load():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        P.builtin_profiles()
