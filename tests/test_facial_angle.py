"""顔の角度連動（F5-9、docs/14 §6.6）: パラメータ契約・Look・出力・式（ToonCore.hlsl の Python ミラー）。"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maya" / "scripts"))

from tdrive_toon import features, look, params  # noqa: E402

TUNABLES = ("_ToonFacialAngle", "_ToonFacialLineWidthSide", "_ToonFacialLineWidthVertical", "_ToonFacialShadeOffsetSide")


def _sample():
    lk = look.new_look("unitychan", "assets/unitychan/unitychan.fbx")
    look.register_part(lk, "face", "face", ["face"])
    return lk


def test_params_defaults_and_ranges():
    p = params.PARAMS_BY_UNITY
    assert p["_ToonFacialAngle"].toggle and p["_ToonFacialAngle"].default == 0.0
    assert (p["_ToonFacialLineWidthSide"].default, p["_ToonFacialLineWidthSide"].min, p["_ToonFacialLineWidthSide"].max) == (1.0, 0.0, 4.0)
    assert (p["_ToonFacialLineWidthVertical"].default, p["_ToonFacialLineWidthVertical"].max) == (1.0, 4.0)
    assert (p["_ToonFacialShadeOffsetSide"].default, p["_ToonFacialShadeOffsetSide"].min, p["_ToonFacialShadeOffsetSide"].max) == (0.0, -1.0, 1.0)
    assert all(t.startswith("_Toon") for t in TUNABLES)


def test_runtime_angles_are_not_a_look_value():
    """角度は実行時の入力: 契約の Specific（= Look・MaterialData に出るもの）には入らず、既定は変化なし。"""
    assert "_ToonFacialAngles" not in params.PARAMS_BY_UNITY
    assert params.RUNTIME_PARAMS["_ToonFacialAngles"] == [0.0, 0.0, 0.0, 0.0]
    assert "_ToonFacialAngles" not in params.default_specific()
    assert len(params.MAYA_RUNTIME_ATTRS["_ToonFacialAngles"]) == 3


def test_feature_owns_the_tunables_and_is_off_by_default():
    f = features.BY_ID["facialAngle"]
    assert f.params == TUNABLES and not f.default_on and f.material_scope


def test_fx_declares_params_and_runtime_inputs():
    fx = (ROOT / "maya/shaders/TDriveToon.fx").read_text(encoding="utf-8")
    for name in [params.PARAMS_BY_UNITY[t].maya for t in TUNABLES] + list(params.MAYA_RUNTIME_ATTRS["_ToonFacialAngles"]):
        assert re.search(rf"^float\s+{name}\b", fx, re.M), name
    # 機能オフでは何も掛けない（従来と同じ出力）: 式の呼び出しは機能のトグルの中だけ
    assert len(re.findall(r"if \(ToonFacialAngle > 0\.5\)\s*\n\s*\w+ [*+]= Toon_Facial", fx)) == 2


def test_formula_exists_only_in_toon_core():
    fx = (ROOT / "maya/shaders/TDriveToon.fx").read_text(encoding="utf-8")
    assert "lerp(1.0, side" not in fx and "abs(angles" not in fx  # 式は ToonCore.hlsl の 1 か所
    core = (ROOT / "shaders/ToonCore.hlsl").read_text(encoding="utf-8")
    assert "saturate(float2(abs(angles.x), abs(angles.y)) * angles.z)" in core
    assert "lerp(1.0, side, k.x) * lerp(1.0, vertical, k.y)" in core
    assert "offsetSide * Toon_FacialK(angles).x" in core


# ---- ToonCore.hlsl の式のミラー（式を変えたらここも変える）
def _sat(x):
    return min(max(x, 0.0), 1.0)


def _k(angles):
    return _sat(abs(angles[0]) * angles[2]), _sat(abs(angles[1]) * angles[2])


def line_factor(angles, side, vertical):
    ky, kp = _k(angles)
    return (1 + (side - 1) * ky) * (1 + (vertical - 1) * kp)


def threshold_offset(angles, offset):
    return offset * _k(angles)[0]


def test_formula_identity_at_defaults_and_zero_angles():
    for a in [(0, 0, 0, 0), (1, 1, 0, 0), (0, 0, 1, 0)]:
        assert line_factor(a, 3.0, 2.0) == 1.0 and threshold_offset(a, 0.5) == 0.0  # 角度なし / 強さ 0 / 正面
    for a in [(0.7, -0.3, 1, 0), (-1, 1, 0.5, 0)]:
        assert line_factor(a, 1.0, 1.0) == 1.0 and threshold_offset(a, 0.0) == 0.0  # 既定値


def test_formula_values():
    assert line_factor((1, 0, 1, 0), 2.0, 0.5) == 2.0  # 真横
    assert line_factor((0, -1, 1, 0), 2.0, 0.5) == 0.5  # 真下
    assert line_factor((0.5, 0, 1, 0), 3.0, 1.0) == 2.0  # 半分
    assert line_factor((-1, 0, 1, 0), 2.0, 1.0) == line_factor((1, 0, 1, 0), 2.0, 1.0)  # 左右対称
    assert threshold_offset((1, 0.9, 1, 0), 0.3) == 0.3 and threshold_offset((0.5, 0, 0.5, 0), -0.4) == -0.1
    assert _k((3, 3, 3, 0)) == (1.0, 1.0)  # saturate


def test_resolve_and_export_follow_the_feature():
    lk = _sample()
    part = mat = "face"
    look.set_feature(lk, "facialAngle", False)
    look.set_value(lk, part, "_ToonFacialAngle", 1.0)
    look.set_value(lk, part, "_ToonFacialLineWidthSide", 2.5)
    sp = look.resolve(lk)[mat]["specific"]
    assert sp["_ToonFacialAngle"] == 0.0 and sp["_ToonFacialLineWidthSide"] == 1.0  # 機能オフ = 効果なし
    assert look.validate(lk) == []
    look.set_feature(lk, "facialAngle", True)
    sp = look.resolve(lk)[mat]["specific"]
    assert sp["_ToonFacialAngle"] == 1.0 and sp["_ToonFacialLineWidthSide"] == 2.5
    props = {s["Property"]: s["Value"] for s in look.to_ddrive_material_data(lk, mat)["Specific"]}
    assert props["_ToonFacialLineWidthSide"] == {"Type": "Float", "FloatValue": 2.5}
    assert props["_ToonFacialAngle"] == {"Type": "Float", "FloatValue": 1.0}
    assert "_ToonFacialAngles" not in props  # 実行時の角度は出さない
    assert "_ToonFacialAngles" not in str(lk)  # Look にも保存しない
