"""共通のテストデータ（tests/facial/conformance/*.json）を全部読んで、Python 実装と比べる。
形式は conformance/README.md。C#（FU-1）も同じファイルを読む。"""

import json
import math
from pathlib import Path

import pytest

from tdrive_facial.core import evaluate as ev
from tdrive_facial.core import space as sp
from tdrive_facial.core.model import BoneOffset

CONF = Path(__file__).parent / "conformance"
WEIGHT_TOL = 1e-4

UE_TESTS = {
    "ExactGridPoint", "BilinearCenter", "EmotionBlend", "ZeroWeightSkip", "EdgeFade", "UnbakedPointFailSoft",
    "ViewAnglesFront", "ViewAnglesRoundTrip", "ExpressionScale", "DistanceFade", "SmoothWeights",
}  # fmt: skip


def load_all():
    files = sorted(CONF.glob("*.json"))
    assert files, "conformance/*.json が無い"
    return {f.name: json.loads(f.read_text(encoding="utf-8")) for f in files}


ALL = load_all()


def cases_of(kind):
    out = []
    for fname, data in ALL.items():
        if data["kind"] == kind:
            for c in data["cases"]:
                out.append(pytest.param(c, id=f"{fname}:{c['name']}"))
    return out


# --- ファイル・ケースの体裁 ---


def test_every_file_has_kind_description_and_cases():
    kinds = set()
    for fname, data in ALL.items():
        assert {"kind", "description", "cases"} <= set(data), fname
        assert isinstance(data["cases"], list) and data["cases"], fname
        kinds.add(data["kind"])
    assert kinds == {"evaluate", "view_angles", "scalar", "smooth", "convert"}


def test_case_names_are_unique_per_file_and_sources_are_marked():
    for fname, data in ALL.items():
        names = [c["name"] for c in data["cases"]]
        assert len(names) == len(set(names)), fname
        for c in data["cases"]:
            src = c["source"]
            assert src == "python-port" or src.startswith("UE FacialCoreTests.cpp:"), (fname, c["name"], src)


def test_all_eleven_ue_automation_tests_are_transcribed():
    seen = set()
    for data in ALL.values():
        for c in data["cases"]:
            if c["source"].startswith("UE FacialCoreTests.cpp:"):
                seen.add(c["source"].split(":", 1)[1])
    assert seen == UE_TESTS


def test_ue_files_only_hold_ue_cases_and_python_files_only_python_cases():
    for c in ALL["evaluate_ue.json"]["cases"]:
        assert c["source"].startswith("UE FacialCoreTests.cpp:")
    for c in ALL["evaluate_python.json"]["cases"]:
        assert c["source"] == "python-port"


def test_required_scenarios_are_covered():
    names = {c["name"] for data in ALL.values() for c in data["cases"]}
    py_eval = {c["name"] for c in ALL["evaluate_python.json"]["cases"]}
    for needle in ("clamp_beyond_range", "edge_fade_partial", "two_emotion_layers", "muted_layer", "disabled", "grid_5x3", "grid_2x2", "grid_7x5"):
        assert any(needle in n for n in py_eval), needle
    view = {c["name"] for c in ALL["view_angles.json"]["cases"]}
    for axis in ("+X", "-X", "+Y", "-Y"):
        assert any(f"axis{axis}" in n and n.startswith("ue_") for n in view), axis
    for axis in ("+Z", "-Z", "+X", "-X"):
        assert any(f"axis{axis}" in n and n.startswith("maya_") for n in view), axis
        assert any(f"axis{axis}" in n and n.startswith("unity_") for n in view), axis
    assert any(c["name"].startswith("series_") for c in ALL["smooth.json"]["cases"])
    assert names


# --- evaluate ---


def run_evaluate(c):
    g = c["grid"]
    grid = ev.GridShape(g["yawRange"], g["pitchRange"], g["cols"], g["rows"], g["edgeFade"])
    layers = [ev.LayerEvalInput(l["morphs"], l["emotionWeight"], l["enabled"]) for l in c["layers"]]
    return {w.morph_name: w.weight for w in ev.evaluate_correction(grid, layers, c["yaw"], c["pitch"])}


@pytest.mark.parametrize("c", cases_of("evaluate"))
def test_evaluate(c):
    tol = c.get("tolerance", WEIGHT_TOL)
    got = run_evaluate(c)
    expect = c["expect"]["weights"]
    assert set(got) == set(expect), (sorted(got), sorted(expect))
    for name, w in expect.items():
        assert got[name] == pytest.approx(w, abs=tol), name


# --- view_angles ---


def run_view(c):
    mode = c["mode"]
    if mode == "direct":
        return ev.compute_view_angles(c["headPos"], c["headForwardYawDeg"], c["viewerPos"])
    if mode == "roundtrip":
        d = ev.compute_view_direction(c["headForwardYawDeg"], c["yawDeg"], c["pitchDeg"])
        viewer = [c["headPos"][i] + d[i] * c["distance"] for i in range(3)]
        return ev.compute_view_angles(c["headPos"], c["headForwardYawDeg"], viewer)
    if mode == "bone":
        return sp.compute_view_angles_in_space(
            c["space"], c["headPos"], c["headRotation"], c["forwardAxis"], c["viewerPos"], c.get("centerOffset", (0, 0, 0))
        )
    raise AssertionError(mode)


@pytest.mark.parametrize("c", cases_of("view_angles"))
def test_view_angles(c):
    yaw, pitch = run_view(c)
    tol = c["toleranceDeg"]
    assert abs(ev.normalize_axis(yaw - c["expect"]["yawDeg"])) <= tol or (
        # 真後ろ（±180）は符号違いも同じ向き
        abs(abs(c["expect"]["yawDeg"]) - 180) < 1e-6 and abs(abs(yaw) - 180) <= tol
    )
    assert pitch == pytest.approx(c["expect"]["pitchDeg"], abs=tol)


# --- scalar ---

SCALARS = {
    "expressionScale": ev.expression_scale,
    "distanceFade": ev.distance_fade,
    "finterpTo": ev.finterp_to,
    "normalizeAxis": ev.normalize_axis,
}


@pytest.mark.parametrize("c", cases_of("scalar"))
def test_scalar(c):
    assert SCALARS[c["fn"]](*c["args"]) == pytest.approx(c["expect"], abs=c.get("tolerance", 1e-6))


# --- smooth ---


@pytest.mark.parametrize("c", cases_of("smooth"))
def test_smooth(c):
    prev = [ev.MorphWeight(w["name"], w["weight"]) for w in c["initial"]]
    for i, step in enumerate(c["steps"]):
        target = [ev.MorphWeight(w["name"], w["weight"]) for w in step["target"]]
        prev = ev.smooth_weights(prev, target, step["dt"], c["speed"], step["snap"])
        got = {w.morph_name: w.weight for w in prev}
        expect = {w["name"]: w["weight"] for w in step["expect"]}
        assert set(got) == set(expect), (i, sorted(got), sorted(expect))
        for name, w in expect.items():
            assert got[name] == pytest.approx(w, abs=WEIGHT_TOL), (i, name)


# --- convert ---


def run_convert(c):
    cv = sp.converter(c["from"], c["to"])
    op, inp = c["op"], c["input"]
    if op == "position":
        return list(cv.position(inp))
    if op == "direction":
        return list(cv.direction(inp))
    if op == "quaternion":
        return list(cv.quaternion(inp))
    if op == "boneOffset":
        b = cv.bone_offset(BoneOffset(tuple(inp["t"]), tuple(inp["r"]), tuple(inp["s"])))
        return {"t": list(b.t), "r": list(b.r), "s": list(b.s)}
    if op == "forwardAxis":
        return cv.forward_axis(inp)
    if op == "mirrorAxis":
        return cv.mirror_axis(inp)
    raise AssertionError(op)


def approx_tree(got, expect, tol):
    if isinstance(expect, dict):
        assert got.keys() == expect.keys()
        for k in expect:
            approx_tree(got[k], expect[k], tol)
    elif isinstance(expect, list):
        assert len(got) == len(expect)
        for a, b in zip(got, expect):
            approx_tree(a, b, tol)
    elif isinstance(expect, str):
        assert got == expect
    else:
        assert math.isclose(got, expect, abs_tol=tol)


@pytest.mark.parametrize("c", cases_of("convert"))
def test_convert(c):
    # 期待値は小数 12 桁に丸めて保存してあるので、許容誤差はそれより少し緩める
    approx_tree(run_convert(c), c["expect"], max(c["tolerance"], 1e-9))
