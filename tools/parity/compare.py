"""描画パリティの比較（docs/09 §5）: Unity と Maya の同条件キャプチャの差を測る。

  uv run --no-project --with pillow --with numpy python tools/parity/compare.py A.png B.png [--out diff.png]

- キャラクター画素だけを比べる: 両画像の四隅から推定した背景色と違う画素（どちらか一方でも）を対象
- 輪郭の縁（アウトライン・アンチエイリアスで 1px ずれやすい所）は除外して最大差を出す
- 合格基準（初期値）: 平均絶対差 ≤ 2/255、縁を除いた最大差 ≤ 8/255
終了コード: 合格 0 / 不合格 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

MEAN_LIMIT = 2 / 255
MAX_LIMIT = 8 / 255
BG_TOLERANCE = 6 / 255


def load(path: str | Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0


def background_mask(img: np.ndarray) -> np.ndarray:
    corners = np.stack([img[0, 0], img[0, -1], img[-1, 0], img[-1, -1]])
    bg = np.median(corners, axis=0)
    return np.all(np.abs(img - bg) <= BG_TOLERANCE, axis=-1)


def dilate(mask: np.ndarray, r: int = 1) -> np.ndarray:
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


def compare(a: np.ndarray, b: np.ndarray) -> dict[str, float | int | bool]:
    if a.shape != b.shape:
        raise ValueError(f"解像度が違う: {a.shape} vs {b.shape}（キャプチャ条件を揃える）")
    char = ~(background_mask(a) & background_mask(b))  # どちらかがキャラクター
    diff = np.abs(a - b).max(axis=-1)
    # 輪郭の縁: キャラクター領域の境界 ±1px と、画素値の急変する所（線・模様の縁）
    edge = dilate(char, 1) & ~(~dilate(~char, 1))
    lum = a.mean(axis=-1)
    grad = np.maximum(np.abs(np.diff(lum, axis=0, prepend=lum[:1])), np.abs(np.diff(lum, axis=1, prepend=lum[:, :1])))
    edge |= dilate(grad > 0.25, 1)
    inner = char & ~edge
    mean = float(diff[char].mean()) if char.any() else 0.0
    mx = float(diff[inner].max()) if inner.any() else 0.0
    return {
        "pixels": int(char.sum()),
        "mean": mean,
        "max_excluding_edges": mx,
        "mean_255": round(mean * 255, 2),
        "max_255": round(mx * 255, 2),
        "pass": mean <= MEAN_LIMIT and mx <= MAX_LIMIT,
    }


def heatmap(a: np.ndarray, b: np.ndarray, gain: float = 16.0) -> Image.Image:
    """差分を強調した画像（黒 = 同じ、赤→黄 = 差が大きい）。"""
    d = np.clip(np.abs(a - b).max(axis=-1) * gain, 0, 1)
    rgb = np.stack([np.clip(d * 2, 0, 1), np.clip(d * 2 - 1, 0, 1), np.zeros_like(d)], axis=-1)
    return Image.fromarray((rgb * 255).astype(np.uint8))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", help="Unity のキャプチャ")
    ap.add_argument("b", help="Maya のキャプチャ")
    ap.add_argument("--out", help="差分ヒートマップの保存先")
    args = ap.parse_args(argv)
    a, b = load(args.a), load(args.b)
    result = compare(a, b)
    if args.out:
        heatmap(a, b).save(args.out)
        result["heatmap"] = args.out
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
