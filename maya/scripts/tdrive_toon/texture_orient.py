"""テクスチャ画像の行の並び順の判定（Maya 非依存）。

Maya ビューポート専用の補正。file ノード → dx11Shader の経路では画像の行が保存順のままシェーダーに渡る。
下から並ぶ画像（TGA の標準・BMP の標準）はそのまま正しく見え、上から並ぶ画像（PNG・JPG など）は V が上下逆に見える。
上から並ぶ画像には、シェーダーの <Slot>FlipV を立てて補正する。Unity はテクスチャを自前で取り込むので影響しない。

判定表（ヘッダーだけを読む）:
  PNG / JPG / JPEG / TIFF  上から並ぶ → True（TIFF は Orientation タグを見ない簡易判定）
  BMP                      高さが負 = 上から → True / 正 = 下から → False
  TGA                      記述子バイト（18 バイト目）の bit 5 が立つ = 上原点 → True / 立たない = 下原点 → False
  EXR / DDS / PSD / HDR 等 未検証のため False（今までの描画のまま）
  読めない・存在しない      False
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

_ALWAYS_TOP_DOWN = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def texture_stored_top_down(path: str | os.PathLike | None) -> bool:
    """画像の行が上から順に保存されているか（True なら FlipV が要る）。不明・読めないときは False。"""
    if not path:
        return False
    p = Path(os.path.expandvars(str(path)))
    ext = p.suffix.lower()
    try:
        if ext in _ALWAYS_TOP_DOWN:
            return p.is_file()
        if ext == ".bmp":
            with p.open("rb") as f:
                head = f.read(26)
            if len(head) < 26 or head[:2] != b"BM":
                return False
            # BITMAPCOREHEADER（12 バイト）は高さが符号なし 16 ビットで、常に下から
            if struct.unpack_from("<I", head, 14)[0] < 40:
                return False
            return struct.unpack_from("<i", head, 22)[0] < 0
        if ext == ".tga":
            with p.open("rb") as f:
                head = f.read(18)
            return len(head) >= 18 and bool(head[17] & 0x20)
    except OSError:
        return False
    return False
