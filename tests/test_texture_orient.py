"""テクスチャの行の並び判定（Maya ビューポート専用の V 反転）と、.fx / preview.py の対応の静的確認。Maya 不要。"""

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maya" / "scripts"))

from tdrive_toon.texture_orient import texture_stored_top_down  # noqa: E402


def _tga(path: Path, descriptor: int) -> Path:
    head = bytearray(18)
    head[2] = 2
    head[17] = descriptor
    path.write_bytes(bytes(head) + b"\x00" * 16)
    return path


def _bmp(path: Path, height: int) -> Path:
    head = b"BM" + struct.pack("<IHHI", 66, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, 2, height, 1, 24, 0, 16, 0, 0, 0, 0)
    path.write_bytes(head + b"\x00" * 16)
    return path


def test_top_down_by_format(tmp_path):
    for name in ("a.png", "a.jpg", "a.JPEG", "a.tif"):
        (tmp_path / name).write_bytes(b"x" * 32)
        assert texture_stored_top_down(tmp_path / name) is True, name
    assert texture_stored_top_down(_tga(tmp_path / "bottom.tga", 0x00)) is False  # 下原点（UnityChan）
    assert texture_stored_top_down(_tga(tmp_path / "bottom8.tga", 0x08)) is False  # 下原点 + アルファ 8 ビット
    assert texture_stored_top_down(_tga(tmp_path / "top.tga", 0x28)) is True
    assert texture_stored_top_down(_bmp(tmp_path / "up.bmp", 2)) is False
    assert texture_stored_top_down(_bmp(tmp_path / "down.bmp", -2)) is True


def test_unknown_or_unreadable_is_false(tmp_path):
    (tmp_path / "a.exr").write_bytes(b"x" * 32)
    (tmp_path / "a.dds").write_bytes(b"x" * 32)
    assert texture_stored_top_down(tmp_path / "a.exr") is False
    assert texture_stored_top_down(tmp_path / "a.dds") is False
    assert texture_stored_top_down(tmp_path / "missing.png") is False
    assert texture_stored_top_down(tmp_path / "missing.tga") is False
    (tmp_path / "short.tga").write_bytes(b"\x00" * 4)
    assert texture_stored_top_down(tmp_path / "short.tga") is False
    assert texture_stored_top_down(None) is False
    assert texture_stored_top_down("") is False


def test_env_var_path_is_expanded(tmp_path, monkeypatch):
    (tmp_path / "t.png").write_bytes(b"x" * 32)
    monkeypatch.setenv("TDRIVE_PROJECT", str(tmp_path))
    assert texture_stored_top_down("$TDRIVE_PROJECT/t.png") is True


def test_every_uv_sampled_slot_has_flipv():
    """メッシュの UV で引く Texture2D（マットキャップ・シャドウ系を除く）は <Slot>FlipV を持ち、サンプルで使っている。"""
    fx = (ROOT / "maya/shaders/TDriveToon.fx").read_text(encoding="utf-8")
    slots = set(re.findall(r"^Texture2D\s+(\w+)\s*<[^>]*ResourceType", fx, re.M))
    slots.discard("ToonMatCapMap")  # 法線から引く（UV ではない）ので対象外
    assert {"BaseMap", "NormalMap", "EmissionMap", "ToonMaskMap", "ToonHairHighlightMap", "ToonFaceShadowMap"} <= slots
    for slot in slots:
        assert re.search(rf"^bool\s+{slot}FlipV\b", fx, re.M), f"{slot}FlipV が無い"
        assert f"{slot}FlipV" in re.sub(rf"^bool\s+{slot}FlipV[^\n]*\n", "", fx, flags=re.M), f"{slot}FlipV を使っていない"
    # すべての Sample は FlipUV を通す（マットキャップとシャドウマップを除く）
    for m in re.finditer(r"(\w+)\.Sample(?:Level)?\(", fx):
        if m.group(1) in ("ToonMatCapMap", "gShadowMap"):
            continue
        assert "FlipUV(" in fx[m.start():m.start() + 200], m.group(0)


def test_preview_sets_flipv_for_every_texture_slot():
    src = (ROOT / "maya/scripts/tdrive_toon/preview.py").read_text(encoding="utf-8")
    assert 'f"{attr}FlipV"' in src and "texture_stored_top_down" in src
    # _set_texture を通らないテクスチャの設定が無い
    assert len(re.findall(r"fileTextureName", src)) >= 1
    assert src.count("_set_texture(") >= 4
