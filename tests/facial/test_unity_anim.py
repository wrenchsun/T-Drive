"""Unity の .anim の読み込み（core/unity_anim.py）。fixtures/unity_anim_*.anim は合成データ（実モデルのファイルではない）。"""

import math
from pathlib import Path

import pytest

from tdrive_facial.core import profile as profile_mod
from tdrive_facial.core import unity_anim as U

FIX = Path(__file__).parent / "fixtures"


def clip(name):
    return U.load(FIX / name)


def test_single_key_clip():
    c = clip("unity_anim_single.anim")
    assert c.name == "synthetic_single"
    assert c.sample_rate == 30
    assert len(c.float_curves) == 3  # m_EditorCurves（編集用の控え）は読まない
    assert ("mdl_face", "blendShape.bs.editor_only") not in c.float_curves
    w = U.to_curve_weights(c, 0.0)
    assert w == {"bs.mouth_open": 1.0, "bs.smile_L": 0.25}  # 値 0 の brow_up は出さない。0〜100 → 0〜1
    # キーが 1 つだけなら時刻に関係なく同じ
    assert U.to_curve_weights(c, 5.0) == w
    assert U.to_curve_weights(c, -1.0) == w


def test_multi_key_linear_interpolation():
    c = clip("unity_anim_multi.anim")
    assert c.length == 2.0
    w0 = U.to_curve_weights(c, 0.0, mesh_path="mdl_face")
    assert "bs.mouth_open" not in w0  # 0
    assert U.to_curve_weights(c, 0.5, "mdl_face")["bs.mouth_open"] == pytest.approx(0.5)
    assert U.to_curve_weights(c, 1.0, "mdl_face")["bs.mouth_open"] == pytest.approx(1.0)
    assert U.to_curve_weights(c, 1.5, "mdl_face")["bs.mouth_open"] == pytest.approx(0.75)
    assert U.to_curve_weights(c, 9.0, "mdl_face")["bs.mouth_open"] == pytest.approx(0.5)  # 最後のキーのあとは端の値


def test_stepped_key_holds_previous_value():
    c = clip("unity_anim_multi.anim")
    assert "bs.step_test" not in U.to_curve_weights(c, 0.9)  # outSlope = Infinity: 次のキーまで 0
    assert U.to_curve_weights(c, 1.0)["bs.step_test"] == pytest.approx(1.0)


def test_hermite_option_uses_tangents():
    c = U.loads(
        "--- !u!74 &1\nAnimationClip:\n  m_FloatCurves:\n  - curve:\n      m_Curve:\n"
        "      - time: 0\n        value: 0\n        inSlope: 0\n        outSlope: 0\n"
        "      - time: 1\n        value: 100\n        inSlope: 0\n        outSlope: 0\n"
        "    attribute: blendShape.bs.a\n    path: m\n"
    )
    lin = U.to_curve_weights(c, 0.25)["bs.a"]
    herm = U.to_curve_weights(c, 0.25, hermite=True)["bs.a"]
    assert lin == pytest.approx(0.25)
    assert herm == pytest.approx(0.15625)  # smoothstep(0.25) = 0.15625
    assert U.to_curve_weights(c, 0.5, hermite=True)["bs.a"] == pytest.approx(0.5)


def test_multiple_paths_and_mesh_filter():
    c = clip("unity_anim_multi.anim")
    assert set(c.mesh_paths()) == {"mdl_face", "mdl_brow"}
    assert U.to_curve_weights(c, 0.0, "mdl_face")["bs.smile_L"] == pytest.approx(0.4)
    assert U.to_curve_weights(c, 0.0, "mdl_brow")["bs.smile_L"] == pytest.approx(0.8)
    assert U.to_curve_weights(c, 0.0, "nothing") == {}
    # パスを省くと、最初に見つかった 0 でない値
    assert U.to_curve_weights(c, 0.0)["bs.smile_L"] == pytest.approx(0.4)
    assert "bs.smile_L" in c.blendshape_names("mdl_brow")


def test_non_blendshape_and_tiny_values_dropped():
    c = clip("unity_anim_multi.anim")
    w = U.to_curve_weights(c, 0.0)
    assert not any("Color" in k or "material" in k for k in w)
    assert "bs.tiny" not in w  # 1e-10 < 1e-6
    assert ("mdl_face", "material._Color.r") in c.float_curves  # 読んではいる（重みにしないだけ）


def test_transform_curves_quaternion_euler_position_scale():
    c = clip("unity_anim_multi.anim")
    bones = U.bone_values(c, 0.0)
    assert set(bones) == {"eye_L", "eye_R"}
    pos, rot, scl = bones["eye_L"]
    assert pos == pytest.approx((0.1, 0.2, 0.3))
    assert rot == pytest.approx((0, 0, 0, 1))
    assert scl == (1.0, 1.0, 1.0)
    pos, rot, scl = bones["eye_R"]
    assert pos is None and scl is None and rot == pytest.approx((0, 0, 0, 1))

    mid = U.bone_values(c, 0.5)["eye_L"]
    assert mid[0] == pytest.approx((0.2, 0.2, 0.1))  # 位置は線形補間
    s = math.sin(math.radians(45) / 2)
    c_ = math.cos(math.radians(45) / 2)
    assert mid[1] == pytest.approx((0.0, s, 0.0, c_), abs=1e-6)  # クォータニオンは補間後に正規化（Y 軸 45°）

    end = U.bone_values(c, 1.0)["eye_L"]
    assert end[1] == pytest.approx((0, 0.7071068, 0, 0.7071068), abs=1e-6)
    assert end[0] == pytest.approx((0.3, 0.2, -0.1))
    # Euler: 時刻 1 = Y 90° の半分 = 45°
    eul = U.bone_values(c, 1.0)["eye_R"][1]
    assert eul == pytest.approx((0.0, math.sin(math.radians(22.5)), 0.0, math.cos(math.radians(22.5))), abs=1e-6)


def test_bone_values_by_path_keeps_hierarchy():
    c = clip("unity_anim_multi.anim")
    by_path = U.bone_values_by_path(c, 0.0)
    assert set(by_path) == {"root/head/eye_L", "root/head/eye_R"}


def test_euler_order_matches_unity():
    # Unity の Quaternion.Euler(x, y, z) は Z → X → Y の順（q = qy · qx · qz）。Z 90° のあと X 90° → 結果は (0.5, -0.5, 0.5, 0.5)
    q = U.euler_to_quat((90, 0, 90), 4)
    assert q == pytest.approx((0.5, -0.5, 0.5, 0.5), abs=1e-9)
    # 単軸ならどの順でも同じ
    for order in range(6):
        assert U.euler_to_quat((0, 60, 0), order) == pytest.approx((0, math.sin(math.radians(30)), 0, math.cos(math.radians(30))))


def test_empty_clip():
    c = clip("unity_anim_empty.anim")
    assert c.is_empty and c.length == 0.0
    assert U.to_curve_weights(c, 0.0) == {}
    assert U.bone_values(c, 0.0) == {}
    assert U.evaluate(c, 1.0).floats == {}


def test_inline_maps_and_odd_formatting():
    text = (
        "%YAML 1.1\r\n%TAG !u! tag:unity3d.com,2011:\r\n--- !u!74 &7400000\r\nAnimationClip:\r\n"
        "  m_Name: odd\r\n  m_FloatCurves:\r\n"
        "    - {curve: {m_Curve: [{time: 0, value: 10}, {time: 2, value: 30}]}, attribute: blendShape.bs.x, path: m}\r\n"
        "  m_PositionCurves:\r\n"
        "  -\r\n"
        "    curve:\r\n"
        "      m_Curve:\r\n"
        "      -\r\n"
        "        time: 0\r\n"
        "        value: {x: 1, y: 2, z: 3}\r\n"
        "    path: a/b\r\n"
    )
    c = U.loads(text)
    assert U.to_curve_weights(c, 1.0)["bs.x"] == pytest.approx(0.2)
    assert U.bone_values(c, 0.0)["b"][0] == (1.0, 2.0, 3.0)


def test_other_documents_and_unknown_sections_are_ignored():
    text = (
        "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: other\n"
        "--- !u!74 &7400000\nAnimationClip:\n  m_Name: c\n  m_ClipBindingConstant:\n    genericBindings:\n    - serializedVersion: 2\n      path: 123\n      attribute: 456\n"
        "  m_AnimationClipSettings:\n    m_StopTime: 2\n"
        "  m_FloatCurves:\n  - curve:\n      m_Curve:\n      - time: 0\n        value: 50\n    attribute: blendShape.bs.y\n    path: m\n"
    )
    c = U.loads(text)
    assert c.name == "c"
    assert U.to_curve_weights(c, 0.0) == {"bs.y": 0.5}


def test_compressed_rotation_is_reported():
    text = "--- !u!74 &1\nAnimationClip:\n  m_CompressedRotationCurves:\n  - path: a\n  m_FloatCurves: []\n"
    c = U.loads(text)
    assert c.warnings and "圧縮" in c.warnings[0]


def test_not_an_anim_raises():
    with pytest.raises(U.UnityAnimError):
        U.loads("{\"format\": \"FacialPose\"}")
    with pytest.raises(U.UnityAnimError):
        U.loads("--- !u!1 &1\nGameObject:\n  m_Name: x\n")


# ---- 名前の対応（R-26）---------------------------------------------------


def test_remap_names():
    out, missing = U.remap_names({"jawOpen": 0.5, "eyeBlinkLeft": 1.0, "other": 0.2}, {"jawOpen": "bs.mouth_open", "eyeBlinkLeft": "bs.eye_close_L"})
    assert out == {"bs.mouth_open": 0.5, "bs.eye_close_L": 1.0, "other": 0.2}
    assert missing == ["other"]
    out, _ = U.remap_names({"a": 0.2, "b": -0.9}, {"a": "x", "b": "x"}, keep_unmapped=False)
    assert out == {"x": -0.9}  # 同じ先は絶対値の大きいほう
    out, _ = U.remap_names({"zz": 1.0}, {}, keep_unmapped=False)
    assert out == {}


def test_build_name_mapping_exact_prefix_case():
    m = U.build_name_mapping(
        ["bs.mouth_open", "smile_l", "EYE_CLOSE_R", "BS.Brow_Up", "unknown_x", "bs.exact"],
        ["bs.mouth_open", "bs.smile_L", "bs.eye_close_R", "bs.brow_up", "bs.exact"],
    )
    assert m.mapping == {
        "bs.mouth_open": "bs.mouth_open",
        "smile_l": "bs.smile_L",
        "EYE_CLOSE_R": "bs.eye_close_R",
        "BS.Brow_Up": "bs.brow_up",
        "bs.exact": "bs.exact",
    }
    assert m.unmatched == ["unknown_x"]


def test_build_name_mapping_without_case_folding_and_ambiguity():
    m = U.build_name_mapping(["Smile"], ["bs.smile"], case_insensitive=False)
    assert m.unmatched == ["Smile"]
    m = U.build_name_mapping(["smile"], ["a.smile", "b.smile"], strip_prefixes=("a.", "b."))
    assert m.ambiguous == {"smile": ["a.smile", "b.smile"]} and not m.mapping


def test_build_mapping_from_profile_uses_standard_names():
    prof = profile_mod.NamingProfile(name="p", standard_curves=["eyeBlinkLeft", "jawOpen"])
    m = U.build_mapping_from_profile(["EYEBLINKLEFT", "JAWOPEN", "foo"], prof, ["eyeBlinkLeft", "jawOpen"])
    assert m.mapping == {"EYEBLINKLEFT": "eyeBlinkLeft", "JAWOPEN": "jawOpen"}
    assert m.unmatched == ["foo"]


def test_wrapped_long_lines_do_not_drop_curves():
    """C-8: Unity が長い行（長いパス・属性名）を折り返して書いても、カーブを黙って落とさない。"""
    long_path = "Armature/Hips/Spine/Chest/Neck/Head/" + "Very_Long_Child_Name/" * 8 + "Face"
    folded = long_path[:60] + "\n      " + long_path[60:120] + "\n      " + long_path[120:]
    text = (
        "--- !u!74 &1\nAnimationClip:\n  m_FloatCurves:\n"
        "  - curve:\n      m_Curve:\n"
        "      - time: 0\n        value: 100\n        inSlope: 0\n        outSlope: 0\n"
        f"    attribute: blendShape.bs.first\n    path: {folded}\n"
        "  - curve:\n      m_Curve:\n"
        "      - time: 0\n        value: 50\n        inSlope: 0\n        outSlope: 0\n"
        "    attribute: blendShape.bs.second\n    path: face\n"
    )
    c = U.loads(text)
    assert {a for (_p, a) in c.float_curves} == {"blendShape.bs.first", "blendShape.bs.second"}
    first = next(fc for (_p, a), fc in c.float_curves.items() if a == "blendShape.bs.first")
    assert first.path.replace(" ", "") == long_path  # 折り返しは空白 1 つでつながる（継ぎ目に空白が入る）
