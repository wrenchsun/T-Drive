"""FBX ファイル（バイナリ）のヘッダーと GlobalSettings を直接読む小さな道具（スモーク用。Maya に読み込まず、書いたままの値を確かめる）。

docs/21 TS-13: FBX のフレームレートを、30 fps のシーンに読み込んだキーの範囲で間接的に見ていた。ここでは TimeMode をファイルから読む。
"""

from __future__ import annotations

import struct
from pathlib import Path

# FBX の TimeMode（FbxTime::EMode）の一部
TIME_MODE_FPS = {3: 60, 5: 48, 6: 30, 10: 29.97, 11: 25, 12: 24}


def version(path: str | Path) -> int:
    """FBX のバージョン番号（FBX 2020 = 7700）。バイナリでなければ 0。"""
    raw = Path(path).read_bytes()[:32]
    return struct.unpack("<I", raw[23:27])[0] if raw.startswith(b"Kaydara FBX Binary") else 0


def time_mode(path: str | Path) -> int | None:
    """GlobalSettings の TimeMode の値。見つからなければ None。"""
    raw = Path(path).read_bytes()
    i = raw.find(b"TimeMode")
    if i < 0:
        return None
    # TimeMode の後ろ: 文字列 "enum"・""・"" の長さ付き文字列、続いて型 'I' と int32
    j = raw.find(b"I", i + 8, i + 8 + 40)
    return struct.unpack("<i", raw[j + 1:j + 5])[0] if j > 0 else None


def fps(path: str | Path) -> float | None:
    m = time_mode(path)
    return TIME_MODE_FPS.get(m) if m is not None else None
