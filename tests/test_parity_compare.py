"""tools/parity/compare.py の検証（pillow / numpy が無ければスキップ）。"""

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "parity"))
import compare  # noqa: E402


def _character(h=200, w=200, shift=0):
    img = np.full((h, w, 3), 0.35, dtype=np.float32)  # 背景（グレー）
    img[50:150, 60 + shift:140 + shift] = [0.9, 0.8, 0.7]  # キャラクター（肌色の四角）
    img[50:150, 58 + shift:60 + shift] = 0.1  # 輪郭線 2px
    return img


def test_identical_images_have_zero_difference():
    a = _character()
    r = compare.compare(a, a.copy())
    assert r["mean"] == 0 and r["max_excluding_edges"] == 0 and r["pass"]


def test_one_pixel_shift_is_forgiven_at_edges():
    r = compare.compare(_character(), _character(shift=1))
    assert r["max_excluding_edges"] == 0  # 縁の 1px ずれは最大差に数えない


def test_color_difference_fails():
    a = _character()
    b = a.copy()
    b[60:140, 70:130] += 0.05  # 約 13/255 の色ずれ
    r = compare.compare(a, b)
    assert not r["pass"] and r["max_255"] > 8


def test_resolution_mismatch_is_an_error():
    with pytest.raises(ValueError):
        compare.compare(_character(200, 200), _character(100, 100))
