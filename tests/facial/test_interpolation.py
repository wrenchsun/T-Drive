"""補間の種類 catmullRom（F5-11、docs/15 §5.u）: 計算・保存・検証（Maya なし）。共通のテストデータは conformance/evaluate_catmullrom.json。"""

import json
import random
from pathlib import Path

import pytest

from tdrive_facial.core import evaluate as ev
from tdrive_facial.core import fcpose_io
from tdrive_facial.core import validate as V
from tdrive_facial.core.model import Document, Quality

ROOT = Path(__file__).resolve().parents[2]


def neutral(rows, cols):
    return ev.LayerEvalInput([f"P{r}_{c}" for r in range(rows) for c in range(cols)], 0.0, True)


def weights(grid, layer, yaw, pitch, sharp=1.0, interp="bilinear"):
    return {w.morph_name: w.weight for w in ev.evaluate_correction(grid, [layer], yaw, pitch, sharp, 1.0, interp)}


def test_basis_sums_to_one_and_hits_the_keys():
    for i in range(21):
        assert sum(ev.catmull_rom_basis(i / 20)) == pytest.approx(1.0)
    assert ev.catmull_rom_basis(0.0) == pytest.approx((0.0, 1.0, 0.0, 0.0))
    assert ev.catmull_rom_basis(1.0) == pytest.approx((0.0, 0.0, 1.0, 0.0))
    assert ev.catmull_rom_basis(0.5) == pytest.approx((-0.0625, 0.5625, 0.5625, -0.0625))


def test_default_is_bilinear_and_equal_at_grid_points():
    g = ev.GridShape(90, 45, 5, 3, 15)
    layer = neutral(3, 5)
    a = ev.evaluate_correction(g, [layer], 33.0, 7.0)
    b = ev.evaluate_correction(g, [layer], 33.0, 7.0, interpolation="bilinear")
    assert a == b
    for r in range(3):
        for c in range(5):
            yaw, pitch = ev.point_angles(90, 45, 5, 3, r, c)
            assert weights(g, layer, yaw, pitch, interp="catmullRom") == pytest.approx(weights(g, layer, yaw, pitch))


def test_weights_are_non_negative_and_sum_to_one_inside_the_grid():
    rnd = random.Random(3)
    for cols, rows in ((5, 3), (2, 2), (7, 5), (1, 3), (3, 1)):
        g = ev.GridShape(90, 45, cols, rows, 15)
        layer = neutral(rows, cols)
        for _ in range(200):
            yaw, pitch = rnd.uniform(-90, 90), rnd.uniform(-45, 45)
            sharp = rnd.choice([1.0, 0.5, 3.0])
            w = weights(g, layer, yaw, pitch, sharp, "catmullRom")
            assert all(v >= 0.0 for v in w.values())
            assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_corner_points_get_weight_from_two_negative_factors():
    # 仕様どおり: 軸ごとの負の重み同士の積は正になり、斜めの遠い点にも少し重みが付く（点ごとに負だけ 0 に丸める）
    g = ev.GridShape(90, 45, 5, 3, 15)
    w = weights(g, neutral(3, 5), 22.5, 11.25, interp="catmullRom")
    assert len(w) == 6 and "P0_1" in w and "P0_4" in w


def test_edge_fade_still_applies():
    g = ev.GridShape(90, 45, 5, 3, 15)
    w = weights(g, neutral(3, 5), 97.5, 0.0, interp="catmullRom")
    assert sum(w.values()) == pytest.approx(0.5)
    assert weights(g, neutral(3, 5), 120.0, 0.0, interp="catmullRom") == {}


def test_quality_roundtrip_omits_the_default():
    doc = Document()
    doc.quality = Quality(sharpness=2.0)
    assert "interpolation" not in fcpose_io.to_dict(doc)["quality"]
    doc.quality.interpolation = "catmullRom"
    d = fcpose_io.to_dict(doc)
    assert d["quality"]["interpolation"] == "catmullRom"
    assert fcpose_io.from_dict(json.loads(json.dumps(d))).quality.interpolation == "catmullRom"
    assert fcpose_io.from_dict({"format": "FacialCorrection", "version": 1, "quality": {"sharpness": 2}}).quality.interpolation == "bilinear"


def test_unknown_value_is_an_error_code():
    doc = Document()
    doc.asset = "x"
    doc.quality = Quality(interpolation="spline")
    issues = [i for i in V.validate(doc, V.SceneInfo()) if i.code == "quality_interpolation_invalid"]
    assert len(issues) == 1 and issues[0].severity == V.SEVERITY_ERROR
    doc.quality.interpolation = "catmullRom"
    assert not [i for i in V.validate(doc, V.SceneInfo()) if i.code == "quality_interpolation_invalid"]


def test_schema_lists_both_values():
    schema = json.loads((ROOT / "schema" / "fcpose.schema.json").read_text(encoding="utf-8"))
    q = schema["properties"]["quality"]["properties"]["interpolation"] if "quality" in schema.get("properties", {}) else None
    if q is None:  # 定義の置き場所が違っても見つける
        text = json.dumps(schema)
        assert '"interpolation"' in text and "catmullRom" in text
    else:
        assert q["enum"] == ["bilinear", "catmullRom"]
