"""tools/setup_sample_shizuku_look.py の Blend 判定（元の .mat の読み取り・PNG の alpha 判定）。Maya 不要。"""

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import setup_sample_shizuku_look as tool  # noqa: E402


def _png(path: Path, ctype: int, rows: list[bytes], width: int, filters: list[int] | None = None) -> Path:
    """8 ビットの PNG を書く。rows は未フィルターの 1 行分のバイト列（filters で行ごとにフィルター種別を指定）。"""
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    raw = b""
    prev = bytes(width * channels)
    for i, row in enumerate(rows):
        f = (filters or [0] * len(rows))[i]
        if f == 0:
            out = row
        elif f == 1:
            out = bytes((row[x] - (row[x - channels] if x >= channels else 0)) & 255 for x in range(len(row)))
        elif f == 2:
            out = bytes((a - b) & 255 for a, b in zip(row, prev))
        else:  # 4 Paeth
            res = bytearray()
            for x in range(len(row)):
                a = row[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                res.append((row[x] - pred) & 255)
            out = bytes(res)
        raw += bytes([f]) + out
        prev = row

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, len(rows), 8, ctype, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def test_png_alpha_states(tmp_path):
    rgb = _png(tmp_path / "rgb.png", 2, [bytes([1, 2, 3] * 3)] * 2, 3)
    assert tool.png_alpha_state(rgb) == "none"
    opaque_row = bytes([10, 20, 30, 255] * 3)
    assert tool.png_alpha_state(_png(tmp_path / "o.png", 6, [opaque_row] * 4, 3)) == "opaque"
    clear = bytes([10, 20, 30, 255, 40, 50, 60, 255, 70, 80, 90, 24])
    for f in (0, 1, 2, 4):  # フィルター種別ごとに復元できる
        rows = [opaque_row, opaque_row, clear]
        assert tool.png_alpha_state(_png(tmp_path / f"p{f}.png", 6, rows, 3, [f] * 3)) == "partial", f
        assert tool.png_alpha_state(_png(tmp_path / f"q{f}.png", 6, [opaque_row] * 3, 3, [f] * 3)) == "opaque", f


def test_parse_mat_and_decide():
    text = "  m_Floats:\n    - _Cutoff: 0.5\n    - _Transparent: 1\n    - _ZWrite: 1\n    - _cullMode: 2\n  m_Colors:\n    - _Color: {r: 1}\n"
    props = tool.parse_unity_mat(text)
    assert props == {"_Cutoff": 0.5, "_Transparent": 1.0, "_ZWrite": 1.0, "_cullMode": 2.0}
    assert tool.blend_from_source(props) == ("Cutout", 0.5, False)
    assert tool.blend_from_source({"_ZWrite": 0.0}) == ("Transparent", 0.5, False)  # オーバーレイ
    assert tool.blend_from_source({"_Transparent": 1.0, "_MaskClipValue": 0.4, "_cullMode": 0.0}) == ("Transparent", 0.4, True)
    assert tool.blend_from_source({"_Transparent": 0.0}) == ("Opaque", 0.5, False)


def test_alpha_rule_and_fallback_table():
    assert tool.apply_alpha_rule("Transparent", "none") == "Opaque"  # wear02: 元は透明だが alpha が無い
    assert tool.apply_alpha_rule("Transparent", "opaque") == "Opaque"
    assert tool.apply_alpha_rule("Cutout", "partial") == "Cutout"
    assert tool.apply_alpha_rule("Opaque", "partial") == "Opaque"
    assert {m: v[0] for m, v in tool.BLEND_FALLBACK.items()} == {
        "mat_body01": "Opaque", "mat_faceOption1": "Transparent", "mat_hair01": "Cutout",
        "mat_wear01": "Transparent", "mat_wear02": "Opaque", "mat_wear03": "Opaque"}
