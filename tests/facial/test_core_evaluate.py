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
