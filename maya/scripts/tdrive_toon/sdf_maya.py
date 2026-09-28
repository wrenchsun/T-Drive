"""SDF 顔影マップの画像入出力（Maya の MImage。Maya 同梱 Python には PIL が無いため）。計算は sdf.py。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from maya.api import OpenMaya as om

from . import sdf

MASK_EXTS = (".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg")


def read_mask(path: str | Path, size: int | None = None) -> np.ndarray:
    """白黒マスクを bool 配列（True = 明るい、上が 0 行目）で読む。size を指定すると正方形に縮小して読む。"""
    img = om.MImage()
    img.readFromFile(str(path))
    if size:
        img.resize(size, size, False)
    return read_gray(img)[::-1, :] >= 0.5  # MImage は下の行から並ぶ


def read_gray(img: om.MImage) -> np.ndarray:
    """MImage の R を 0〜1 で返す（行は MImage の並び = 下から）。"""
    import ctypes

    w, h = img.getSize()
    ptr = img.pixels()
    # Maya 2026 の API 2.0 では pixels() がバイト列でなくアドレス（int）を返す
    raw = ctypes.string_at(ptr, w * h * 4) if isinstance(ptr, int) else bytes(ptr)
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4)[..., 0] / 255.0


def write_map(value: np.ndarray, path: str | Path) -> str:
    """0〜1 の配列を R=G=B のグレー 8bit PNG で書く（シェーダーは R を使う）。"""
    h, w = value.shape
    v = np.clip(np.round(value[::-1, :] * 255.0), 0, 255).astype(np.uint8)
    rgba = np.empty((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = v
    rgba[..., 3] = 255
    img = om.MImage()
    img.create(w, h, 4, om.MImage.kByte)
    img.setPixels(bytearray(rgba.tobytes()), w, h)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.writeToFile(str(path), "png")
    return str(path)


def find_masks(folder: str | Path) -> list[tuple[float, Path]]:
    out = []
    for p in sorted(Path(folder).iterdir()):
        if p.suffix.lower() in MASK_EXTS and "sdf" not in p.stem.lower():
            out.append((sdf.angle_from_filename(p.stem), p))
    if len(out) < 2:
        raise RuntimeError(f"角度付きのマスクが 2 枚以上必要です（例: face_shadow_000.png, face_shadow_090.png）: {folder}")
    return sorted(out)


def generate(folder: str | Path, out_path: str | Path | None = None, size: int = 512) -> str:
    """フォルダの角度別マスクから SDF 顔影マップを作る（既定 512×512。大きいほど時間がかかる）。"""
    masks = find_masks(folder)
    arrays = [read_mask(p, size) for _a, p in masks]
    value = sdf.combine(arrays, [a for a, _p in masks])
    out = Path(out_path) if out_path else Path(folder) / "face_shadow_sdf.png"
    return write_map(value, out)
