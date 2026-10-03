"""tdrive_facial.core の計算（evaluate）と Maya 非依存の検査。
UE 版 FacialCoreTests.cpp の 11 件そのものは conformance/ のデータで test_conformance.py が確かめる。ここは補足。"""

import ast
import math
import random
import sys
from pathlib import Path

import pytest

from tdrive_facial.core import evaluate as ev

PKG = Path(__file__).resolve().parents[2] / "maya" / "scripts" / "tdrive_facial"
FORBIDDEN_ROOTS = {"maya", "pymel", "PySide2", "PySide6", "shiboken2", "shiboken6", "tdrive_toon", "tdrive"}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level


def test_core_does_not_import_maya_or_qt():
    files = sorted((PKG / "core").glob("*.py")) + [PKG / "__init__.py"]
    assert len(files) >= 7
    bad = []
    for f in files:
        for module, level in _imports(f):
            if level == 0 and module.split(".")[0] in FORBIDDEN_ROOTS:
                bad.append(f"{f.name}: {module}")
    assert not bad, bad


def test_core_source_has_no_maya_import_text():
    # ast に見えない書き方（__import__ や文字列）も含め、`import maya` / `from maya` の文字が無いこと
    for f in sorted(PKG.rglob("core/*.py")) + [PKG / "__init__.py"]:
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import maya", "from maya")), f"{f.name}: {line}"
            assert "__import__(" not in stripped, f"{f.name}: {line}"


def test_core_uses_only_standard_library():
    stdlib = set(sys.stdlib_module_names)
    for f in (PKG / "core").glob("*.py"):
        for module, level in _imports(f):
            if level == 0:
                assert module.split(".")[0] in stdlib, f"{f.name}: {module}"


def test_importing_core_does_not_load_maya():
    import tdrive_facial.core.evaluate  # noqa: F401
    import tdrive_facial.core.fcpose_io  # noqa: F401
    import tdrive_facial.core.model  # noqa: F401
    import tdrive_facial.core.naming  # noqa: F401
    import tdrive_facial.core.space  # noqa: F401

    assert "maya.cmds" not in sys.modules


# --- evaluate ---


def _grid(cols=3, rows=3, yaw=90.0, pitch=45.0, fade=15.0):
    return ev.GridShape(yaw, pitch, cols, rows, fade)


def _neutral(cols=3, rows=3, prefix="N"):
    return ev.LayerEvalInput([f"{prefix}_R{r}_C{c}" for r in range(rows) for c in range(cols)])


def _as_dict(ws):
    return {w.morph_name: w.weight for w in ws}


def test_weights_sum_to_one_inside_grid_for_neutral_only():
    rng = random.Random(1)
    g = _grid(5, 3)
    layer = _neutral(5, 3)
    for _ in range(200):
        yaw = rng.uniform(-90, 90)
        pitch = rng.uniform(-45, 45)
        total = sum(w.weight for w in ev.evaluate_correction(g, [layer], yaw, pitch))
        assert total == pytest.approx(1.0, abs=1e-9)


def test_emotion_layer_adds_on_top_and_neutral_stays_full():
    g = _grid()
    layers = [_neutral(prefix="N"), ev.LayerEvalInput([f"E_R{r}_C{c}" for r in range(3) for c in range(3)], 0.3)]
    out = _as_dict(ev.evaluate_correction(g, layers, -45, -22.5))
    assert sum(v for k, v in out.items() if k.startswith("N_")) == pytest.approx(1.0)
    assert sum(v for k, v in out.items() if k.startswith("E_")) == pytest.approx(0.3)


def test_output_order_is_first_appearance():
    g = _grid()
    layers = [_neutral(prefix="N"), ev.LayerEvalInput([f"E_R{r}_C{c}" for r in range(3) for c in range(3)], 1.0)]
    out = ev.evaluate_correction(g, layers, -90, -45)
    assert [w.morph_name for w in out] == ["N_R0_C0", "E_R0_C0"]


def test_evaluate_does_not_mutate_inputs():
    layer = _neutral()
    names = list(layer.corner_morph_names)
    ev.evaluate_correction(_grid(), [layer], 10, 10)
    assert list(layer.corner_morph_names) == names


def test_compute_grid_cell_matches_evaluate_axes():
    info = ev.compute_grid_cell(_grid(5, 3), 22.5, 11.25)
    assert (info.col0, info.col1, info.row0, info.row1) == (2, 3, 1, 2)
    assert info.col_frac == pytest.approx(0.5)
    assert info.row_frac == pytest.approx(0.25)
    assert info.fade_scale == 1.0
    assert ev.compute_grid_cell(_grid(0, 3), 0, 0) == ev.GridCellInfo()


def test_point_angles_row0_is_minus_pitch_and_center_is_front():
    assert ev.point_angles(90, 45, 5, 3, 0, 0) == (-90, -45)
    assert ev.point_angles(90, 45, 5, 3, 1, 2) == (0, 0)
    assert ev.point_angles(90, 45, 5, 3, 2, 4) == (90, 45)
    assert ev.point_angles(90, 45, 1, 1, 0, 0) == (0, 0)


def test_point_angles_are_exact_grid_points_of_evaluate():
    g = _grid(5, 3)
    layer = _neutral(5, 3)
    for r in range(3):
        for c in range(5):
            yaw, pitch = ev.point_angles(90, 45, 5, 3, r, c)
            out = ev.evaluate_correction(g, [layer], yaw, pitch)
            assert [(w.morph_name, round(w.weight, 9)) for w in out] == [(f"N_R{r}_C{c}", 1.0)]


@pytest.mark.parametrize(
    "angle,expected",
    [(0, 0), (180, 180), (-180, 180), (181, -179), (-181, 179), (360, 0), (720.5, 0.5), (-540, 180)],
)
def test_normalize_axis(angle, expected):
    assert ev.normalize_axis(angle) == pytest.approx(expected)


@pytest.mark.parametrize("axis,offset", [("+X", 0), ("-X", 180), ("+Y", 90), ("-Y", -90)])
def test_forward_axis_yaw_offset(axis, offset):
    assert ev.forward_axis_yaw_offset_deg(axis) == offset


def test_forward_axis_yaw_offset_rejects_z_and_garbage():
    for bad in ("+Z", "-Z", "X", ""):
        with pytest.raises(ValueError):
            ev.forward_axis_yaw_offset_deg(bad)


def test_view_angles_round_trip_random():
    rng = random.Random(7)
    for _ in range(300):
        head = [rng.uniform(-500, 500) for _ in range(3)]
        fwd = rng.uniform(-180, 180)
        yaw = rng.uniform(-179, 179)
        pitch = rng.uniform(-89, 89)
        d = ev.compute_view_direction(fwd, yaw, pitch)
        assert sum(c * c for c in d) == pytest.approx(1.0)
        dist = rng.uniform(1, 1000)
        viewer = [head[i] + d[i] * dist for i in range(3)]
        y2, p2 = ev.compute_view_angles(head, fwd, viewer)
        assert y2 == pytest.approx(yaw, abs=1e-6)
        assert p2 == pytest.approx(pitch, abs=1e-6)


def test_should_snap():
    assert ev.should_snap(None, 0, 0, 45)
    assert not ev.should_snap((0, 0), 10, 10, 45)
    assert ev.should_snap((0, 0), 50, 0, 45)
    assert ev.should_snap((0, 0), 0, -50, 45)
    # Yaw は ±180 をまたいでも差で見る
    assert not ev.should_snap((175, 0), -175, 0, 45)
    assert ev.should_snap((90, 0), -90, 0, 45)
    # 閾値ちょうどはスナップしない（> で判定）
    assert not ev.should_snap((0, 0), 45, 0, 45)


def test_expression_scale_and_distance_fade_edges():
    assert ev.expression_scale(0.5, 0.5) == pytest.approx(0.75)
    assert ev.expression_scale(2.0, 2.0) == 0.0
    assert ev.distance_fade(100, 100, 200) == 1.0
    assert ev.distance_fade(200, 100, 200) == 0.0
    assert ev.distance_fade(1, 200, 100) == 1.0


def test_finterp_to_matches_unreal_formula():
    # UE: dist = target - current; if dist^2 < 1e-8 → target; current + dist * clamp(dt * speed, 0, 1)
    assert ev.finterp_to(0, 1, 0.1, 5) == pytest.approx(0.5)
    assert ev.finterp_to(0, 1, 1.0, 5) == 1.0
    assert ev.finterp_to(0.2, 1.0, 0.1, 0) == 1.0
    assert ev.finterp_to(1.0, 1.0 + 5e-5, 0.1, 5) == 1.0 + 5e-5  # dist^2 = 2.5e-9 < 1e-8
    assert ev.finterp_to(0, 1, -1.0, 5) == 0.0  # 負の dt は 0 に丸める


def test_smooth_weights_converges_and_drops_faded_names():
    cur = [ev.MorphWeight("A", 1.0)]
    for _ in range(200):
        cur = ev.smooth_weights(cur, [], 1 / 30, 10.0, False)
        if not cur:
            break
    assert cur == []


def test_smooth_weights_keeps_explicit_zero_target():
    out = ev.smooth_weights([ev.MorphWeight("A", 0.0)], [ev.MorphWeight("A", 0.0)], 0.1, 10, False)
    assert [(w.morph_name, w.weight) for w in out] == [("A", 0.0)]


def test_smooth_weights_is_pure():
    prev = [ev.MorphWeight("A", 0.5)]
    target = [ev.MorphWeight("A", 1.0), ev.MorphWeight("B", 1.0)]
    ev.smooth_weights(prev, target, 0.1, 5, False)
    assert prev[0].weight == 0.5 and target[0].weight == 1.0


def test_pipeline_scales_compose_like_the_runtime():
    # 表情の弱め × 距離フェード × 全体の強さ（docs/14 §6.2 の 6）を掛けたあとスムージング
    g = _grid()
    base = ev.evaluate_correction(g, [_neutral()], -45, -22.5)
    scale = ev.expression_scale(0.5, 1.0) * ev.distance_fade(150, 100, 200) * 0.8
    assert scale == pytest.approx(0.5 * 0.5 * 0.8)
    target = [ev.MorphWeight(w.morph_name, w.weight * scale) for w in base]
    out = ev.smooth_weights([], target, 1 / 30, 0, False)
    assert sum(w.weight for w in out) == pytest.approx(scale)


def test_math_helpers_clamp_and_nearly_zero():
    assert ev.clamp(5, 0, 1) == 1 and ev.clamp(-5, 0, 1) == 0 and ev.clamp(0.5, 0, 1) == 0.5
    assert ev.is_nearly_zero(1e-9) and not ev.is_nearly_zero(1e-7)
    assert math.isclose(ev.clamp_axis(-90), 270)


# --- F5: シャープ化・コマ打ち・距離の重み・誇張 ---

_F5G2 = ev.GridShape(90, 45, 2, 1, 15)


def _f5e(layers, yaw=-45.0, pitch=0.0, **kw):
    return {w.morph_name: w.weight for w in ev.evaluate_correction(_F5G2, layers, yaw, pitch, **kw)}


def _f5n(ex=None):
    return ev.LayerEvalInput(["A", "B"], 0.0, True, ex)


def test_sharpness_default_is_bit_for_bit_the_old_path():
    base = _f5e([_f5n()])
    assert _f5e([_f5n()], sharpness=1.0) == base
    assert base == {"A": 0.75, "B": 0.25}


def test_sharpness_two_splits_25_75_into_10_90():
    got = _f5e([_f5n()], sharpness=2.0)
    assert got["A"] == pytest.approx(0.9) and got["B"] == pytest.approx(0.1)


def test_sharpness_large_snaps_and_is_clamped():
    assert _f5e([_f5n()], sharpness=64.0) == {"A": pytest.approx(1.0)}
    assert _f5e([_f5n()], sharpness=1e6) == _f5e([_f5n()], sharpness=64.0)
    assert _f5e([_f5n()], sharpness=0.0) == _f5e([_f5n()], sharpness=0.01)


def test_sharpness_keeps_total_one_and_zero_corners_zero():
    w = ev.sharpen_weights([0.5625, 0.1875, 0.1875, 0.0], 3.0)
    assert sum(w) == pytest.approx(1.0) and w[3] == 0.0
    assert ev.sharpen_weights([0.0, 0.0], 2.0) == (0.0, 0.0)
    assert ev.sharpen_weights([0.3, 0.7], 1.0) == (0.3, 0.7)


def test_sharpness_is_applied_before_edge_fade_and_layer_scale():
    g = ev.GridShape(90, 45, 2, 1, 15)
    layers = [ev.LayerEvalInput(["A", "B"], 0, True), ev.LayerEvalInput(["H_A", "H_B"], 0.5, True)]
    got = {w.morph_name: w.weight for w in ev.evaluate_correction(g, layers, -97.5, 0, sharpness=2.0)}
    assert got["A"] == pytest.approx(0.5) and got["H_A"] == pytest.approx(0.25)  # 範囲外は端の点に寄る（フェード 0.5・感情 0.5）


def test_sharpness_unbaked_corner_does_not_renormalise_others():
    got = _f5e([ev.LayerEvalInput(["A", None], 0.0, True)], sharpness=2.0)
    assert got == {"A": pytest.approx(0.9)}


def test_layer_weight_from_distance():
    f = ev.layer_weight_from_distance
    assert f(1, 2, 6, 0, 1) == 0 and f(4, 2, 6, 0, 1) == 0.5 and f(9, 2, 6, 0, 1) == 1
    assert f(4, 2, 6, 1, 0) == 0.5
    assert f(1.9, 2, 2, 0.2, 0.9) == 0.2 and f(2, 2, 2, 0.2, 0.9) == 0.9


def test_step_gate_basic_flow():
    assert ev.step_gate(0.0, 0.016, 0) == (True, 0.0)
    ok, a = ev.step_gate(0.0, 0.05, 10)
    assert (ok, a) == (False, pytest.approx(0.05))
    ok, a = ev.step_gate(a, 0.05, 10)
    assert ok and a == 0.0
    ok, a = ev.step_gate(0.0, 0.01, 10, force=True)
    assert ok and a == pytest.approx(0.01)


def test_step_gate_long_run_keeps_the_average_rate():
    accum, n = 0.0, 0
    for _ in range(6000):  # 60fps で 100 秒
        ok, accum = ev.step_gate(accum, 1 / 60, 12)
        n += ok
    assert abs(n - 1200) <= 1


def test_exaggeration_scales_only_ex_shapes():
    layers = [_f5n(["A_Ex", "B_Ex"])]
    full = _f5e(layers)
    assert full == {"A": 0.75, "B": 0.25, "A_Ex": 0.75, "B_Ex": 0.25}
    half = _f5e(layers, exaggeration=0.5)
    assert half["A"] == 0.75 and half["A_Ex"] == pytest.approx(0.375)
    assert set(_f5e(layers, exaggeration=0.0)) == {"A", "B"}


def test_exaggeration_without_ex_names_changes_nothing():
    assert _f5e([_f5n()], exaggeration=0.3) == _f5e([_f5n()])


def test_exaggeration_skips_ex_when_main_shape_missing():
    assert _f5e([ev.LayerEvalInput([None, "B"], 0.0, True, ["A_Ex", "B_Ex"])]) == {"B": 0.25, "B_Ex": 0.25}


def test_quality_new_keys_round_trip_and_defaults():
    import json

    from tdrive_facial.core import fcpose_io as io
    from tdrive_facial.core.model import Quality

    assert Quality().exaggeration == 1.0
    d = {"format": "FacialCorrection", "version": 1, "layers": [{"name": "Neutral"}],
         "quality": {"sharpness": 2.0, "stepFps": 12, "exaggeration": 0.5, "angleEpsilon": 0.1, "maxLod": 0, "q": 1},
         "layerWeights": {"Happy": {"source": "distance", "start": 1, "end": 5, "from": 0, "to": 1, "x": 2}}}
    doc = io.from_dict(json.loads(json.dumps(d)))
    assert doc.quality.exaggeration == 0.5 and doc.quality.sharpness == 2.0 and doc.quality.step_fps == 12
    out = json.loads(io.dumps(doc))
    assert out["quality"] == d["quality"] and out["layerWeights"] == d["layerWeights"]
    # キー無し（UE 版のファイル）は既定値。既定の誇張は書き出さない
    d2 = {"format": "FacialCorrection", "version": 1, "layers": [{"name": "Neutral"}], "quality": {"sharpness": 1.5}}
    doc2 = io.from_dict(d2)
    assert doc2.quality.exaggeration == 1.0 and doc2.quality.step_fps == 0.0
    assert "exaggeration" not in json.loads(io.dumps(doc2))["quality"]
