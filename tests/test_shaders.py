"""シェーダーのオフラインコンパイル確認（Windows SDK の fxc がある環境のみ）。"""

import glob
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FXC = sorted(glob.glob(r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\fxc.exe"))
needs_fxc = pytest.mark.skipif(not FXC, reason="fxc.exe (Windows SDK) が無い")


def _fxc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([FXC[-1], "/nologo", *args], capture_output=True, text=True)


@needs_fxc
@pytest.mark.parametrize("name", ["TDriveToon.fx", "TDriveContactShadow.fx", "TDriveScreenLine.fx"])
def test_maya_fx_compiles(tmp_path, name):
    r = _fxc("/T", "fx_5_0", "/D", "_MAYA_", "/Fo", str(tmp_path / "out.fxo"), str(ROOT / "maya/shaders" / name))
    assert r.returncode == 0, r.stdout + r.stderr


@needs_fxc
def test_core_compiles_standalone(tmp_path):
    """ToonCore.hlsl が Maya ラッパー無しでも（= Unity からの include と同条件で）コンパイルできる。"""
    src = tmp_path / "harness.hlsl"
    src.write_text(
        f'#include "{(ROOT / "shaders/ToonCore.hlsl").as_posix()}"\n'
        "float4 main(float4 c : COLOR0, float3 n : NORMAL) : SV_Target {\n"
        "  float4 m = Toon_CombineMask(c, c);\n"
        "  float lit = Toon_LitFactor(normalize(n), float3(0,1,0), m, 0.5, 0.02, 1.0);\n"
        "  float3 col = Toon_Shade(c.rgb, lit, float3(0.8,0.7,0.9), float3(1,1,1));\n"
        "  col = Toon_ApplyTint(col, m, float3(1,0.6,0.6), 0.5);\n"
        "  col += Toon_SmoothNormalWS(c.xy, normalize(n), float3(1,0,0), 1.0) * 0.001;\n"
        "  col += Toon_OutlineColor(col, float3(0.2,0.2,0.2), 0.5) * 0.001;\n"
        "  col.xy += Toon_OutlineClipOffset(c.xy, 1.0, m, 1.0, float2(1920, 1080)) * 0.001;\n"
        "  col *= Toon_LightColor(float3(1,0.9,0.8), 0.5);\n"
        "  col += Toon_Rim(normalize(n), float3(0,0,1), 4.0, 0.5, lit);\n"
        "  col.xy += Toon_HairHighlightUV(c.xy, float3(0,0,1), float3(0,1,0), 0.1) * 0.001;\n"
        "  col += Toon_DepthOffsetWS(c.xyz, float3(0,1,5), 0.03) * 0.001;\n"
        "  col *= Toon_OutlineDistanceFactor(5.0, 2.0, 0.5);\n"
        "  float l2 = Toon_Lit2Factor(Toon_ShadeInput(normalize(n), float3(0,1,0), m), 0.25, 0.02, 1.0);\n"
        "  col = Toon_Shade2(col, lit, l2, float3(0.8,0.7,0.9), float3(0.6,0.5,0.7), float3(1,1,1));\n"
        "  col = Toon_ColorCorrect(col, 1.2, 0.9);\n"
        "  col.xy += Toon_MatCapUV(normalize(n)) * 0.001;\n"
        "  col *= Toon_OutlineDirectionFactor(normalize(n), float3(0,1,0), 1.5, 1.2);\n"
        "  col += Toon_DepthCompressVS(c.xyz, float3(0,0,-3), 0.5) * 0.001;\n"
        "  col *= Toon_FaceShadowLit(lit, c.r, Toon_FaceLightAngle01(float3(0,0,1), normalize(n)), 0.02, 1.0, 1.0);\n"
        "  col.xy += Toon_FaceShadowUV(c.xy, float3(-1,0,0), normalize(n)) * 0.001;\n"
        "  col *= 1.0 - 0.5 * Toon_LineSeeThroughPair(c, c.wzyx, c.yxwz, 0.05, 2u);\n"
        "  col *= 1.0 - 0.5 * Toon_LineSilhouettePart(c, c.wzyx);\n"
        "  return float4(Toon_Tonemap(col, TOON_TONEMAP_NEUTRAL), 1);\n"
        "}\n",
        encoding="utf-8",
    )
    r = _fxc("/T", "ps_5_0", "/E", "main", "/Fo", str(tmp_path / "out.cso"), str(src))
    assert r.returncode == 0, r.stdout + r.stderr


def test_fx_uniforms_match_contract():
    """Maya ラッパーがパラメータ契約の全パラメータを uniform として持っている。"""
    import re
    import sys

    sys.path.insert(0, str(ROOT / "maya" / "scripts"))
    from tdrive_toon import params

    fx = (ROOT / "maya/shaders/TDriveToon.fx").read_text(encoding="utf-8")
    declared = set(re.findall(r"^(?:float4|float|Texture2D|bool|int|float3)\s+(\w+)", fx, re.M))
    missing = {p.maya for p in params.SPECIFIC_PARAMS} - declared
    assert not missing, f".fx に無いパラメータ: {missing}"


def test_fx_has_common_texture_uniforms():
    """テクスチャの差し替え（4-9 / 4-10）: preview.py が設定する Common の uniform が .fx にある。"""
    fx = (ROOT / "maya/shaders/TDriveToon.fx").read_text(encoding="utf-8")
    for name in ("BaseMap", "NormalMap", "NormalMapEnabled", "NormalScale", "EmissionMap", "EmissionMapEnabled", "EmissionColor", "EmissionIntensity"):
        assert re.search(rf"^\w+\s+{name}\b", fx, re.M), name


def test_opaque_techniques_cast_shadows():
    """セルフシャドウ（T-43）: 不透明の技法に影を落とすパス（shadowPass）がある。無いと影用ライトに映らない。"""
    fx = (ROOT / "maya/shaders/TDriveToon.fx").read_text(encoding="utf-8")
    for tech in ("Opaque", "OpaqueDoubleSided"):
        body = fx.split(f"technique11 {tech}\n", 1)[1].split("technique11", 1)[0]
        assert 'drawContext = "shadowPass"' in body, tech
    assert "SHADOWMAP" in fx and "SHADOWMAPMATRIX" in fx
