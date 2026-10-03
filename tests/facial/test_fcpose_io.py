""".fcpose.json の読み書き（fcpose_io.py）・スキーマ（schema/fcpose.schema.json）。"""

import copy
import json
import warnings
from pathlib import Path

import pytest

from tdrive_facial.core import fcpose_io as io
from tdrive_facial.core import model as m

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
ROOT = HERE.parents[1]
SCHEMA_PATH = ROOT / "schema" / "fcpose.schema.json"

UE_POSE = FIX / "ue_single_pose.fcpose.json"
UE_FULL = FIX / "ue_full_asset.fcpose.json"
TD_FULL = FIX / "tdrive_maya_full.fcpose.json"
ALL_FIXTURES = [UE_POSE, UE_FULL, TD_FULL]


def raw(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


# --- 往復 ---


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_round_trip_is_semantically_identical(path):
    original = raw(path)
    doc = io.load(path)
    assert json.loads(io.dumps(doc)) == original


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_dump_is_stable_and_idempotent(path):
    text = io.dumps(io.load(path))
    assert io.dumps(io.loads(text)) == text  # 書いたものを読んで書いても同じ
    assert io.dumps(io.load(path)) == text  # 何度でも同じ
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert "\r" not in text and "\t" not in text
    assert not text.startswith("﻿")


def test_ue_files_keep_ue_key_order_and_add_no_keys():
    out = io.to_dict(io.load(UE_FULL))
    assert list(out) == [
        "format", "version", "meta", "grid", "policy", "layers", "workingSet", "mirror", "autogen", "exclude",
        "intensityCurves", "profile",
    ]  # fmt: skip
    assert list(out["grid"]) == ["yawRange", "pitchRange", "cols", "rows", "baseBone", "forwardAxis", "centerOffset", "edgeFade"]
    assert list(out["layers"][0]["points"][0]) == ["row", "col", "isKey", "curves", "bones"]
    assert list(io.to_dict(io.load(UE_POSE))) == ["format", "version", "meta", "curves", "bones"]
    # 追加キーは UE 版のファイルに増えない
    assert not {"asset", "target", "bake", "limits", "material", "quality", "perspective", "layerWeights", "sculptShapes"} & set(out)


def test_tdrive_keys_order_after_ue_keys():
    out = io.to_dict(io.load(TD_FULL))
    keys = list(out)
    assert keys[:12] == [
        "format", "version", "meta", "grid", "policy", "layers", "workingSet", "mirror", "autogen", "exclude",
        "intensityCurves", "profile",
    ]  # fmt: skip
    assert keys[12:] == [
        "asset", "target", "bake", "limits", "material", "quality", "perspective", "layerWeights", "sculptShapes",
        "futureFeature",
    ]  # fmt: skip


def test_ue_single_pose_model_values():
    doc = io.load(UE_POSE)
    assert isinstance(doc, m.PoseDocument)
    assert (doc.meta.unit, doc.meta.up_axis, doc.meta.handedness, doc.meta.source) == ("cm", "Z", "left", "UE5.8")
    assert doc.pose.curves["jaw_open"] == pytest.approx(0.055331829935312271)
    b = doc.pose.bones["bone_neck"]
    assert b.t == (0, 0, 0) and b.s == (1, 1, 1)
    assert b.r == (1.4456707412421908e-09, -0.34202014078474741, 3.2027757558704957e-10, 0.93969262171072809)


def test_ue_full_asset_model_values():
    doc = io.load(UE_FULL)
    assert isinstance(doc, m.Document)
    assert (doc.grid.cols, doc.grid.rows, doc.grid.forward_axis, doc.grid.base_bone) == (5, 3, "+X", "head")
    assert doc.grid.center_offset == (0, 0, 3.5)
    assert [l.name for l in doc.layers] == ["Neutral", "Joy"]
    assert doc.layers[1].emotion_curve == "emo_joy"
    assert sorted(doc.layers[0].points) == [(0, 4), (1, 0), (1, 3), (1, 4), (2, 2)]
    assert doc.layers[0].points[(1, 0)].is_key is False and doc.layers[0].points[(1, 4)].is_key is True
    assert doc.mirror.bone_axis == "Y" and doc.autogen.mode == "IDW" and doc.profile == "shizuku"
    assert doc.exclude.curves == ["lipSync_*"]
    # T-Drive の追加キーは None（JSON に無い）
    assert doc.asset is None and doc.target is None and doc.limits is None and doc.sculpt_shapes is None


def test_tdrive_full_model_values():
    doc = io.load(TD_FULL)
    assert doc.asset == "shizuku" and doc.target.mesh == "mdl_face02" and doc.target.extra_meshes == ["mdl_eyelash"]
    assert doc.bake.delta_threshold == 0.001 and doc.bake.differential is True
    assert doc.limits == {"bs.mouth_smile_L": (0, 1), "bs.jaw_open": (0, 2)}
    assert doc.material.mode == "propertyBlock"
    assert doc.layer_weights == {"Joy": {"source": "curve"}}
    assert doc.sculpt_shapes.prefix == "fcs_"
    assert doc.grid.forward_axis == "+Z"  # ±Z を受け付ける
    assert doc.layers[0].points[(1, 4)].pose.curves == {"bs.mouth_smile_L": 0.4}


# --- 未知キーの保持 ---


def test_unknown_keys_are_preserved_at_every_level():
    d = raw(TD_FULL)
    d["topUnknown"] = {"a": [1, 2, {"b": None}]}
    d["meta"]["metaUnknown"] = 1
    d["grid"]["gridUnknown"] = "g"
    d["policy"]["policyUnknown"] = [True]
    d["workingSet"] = {"curves": [], "bones": [], "wsUnknown": 1}
    d["mirror"]["mirrorUnknown"] = 1
    d["autogen"]["autogenUnknown"] = 1
    d["exclude"] = {"curves": [], "bones": [], "exUnknown": 1}
    d["layers"][0]["layerUnknown"] = {"x": 1}
    d["layers"][0]["points"][0]["pointUnknown"] = 5
    d["layers"][0]["points"][1]["bones"]["bone_eye_L"]["boneUnknown"] = [1]
    for key in ("target", "bake", "material", "quality", "perspective", "sculptShapes"):
        d.setdefault(key, {})["nested_" + key] = 1
    d["limits"]["bs.new"] = [0, 1]
    doc = io.from_dict(copy.deepcopy(d))
    assert doc.extra["topUnknown"] == {"a": [1, 2, {"b": None}]}
    out = json.loads(io.dumps(doc))
    for k, v in d.items():
        assert out[k] == v, k


def test_extra_is_deep_copied_and_not_aliased():
    d = raw(TD_FULL)
    doc = io.from_dict(d)
    doc.extra["futureFeature"]["a"] = 99
    assert d["futureFeature"]["a"] == 1
    out = io.to_dict(doc)
    out["futureFeature"]["nested"].append(1)
    assert len(doc.extra["futureFeature"]["nested"]) == 2


def test_unknown_keys_in_pose_document():
    d = raw(UE_POSE)
    d["extraTop"] = "x"
    d["bones"]["bone_neck"]["extraBone"] = 1
    d["meta"]["extraMeta"] = 2
    assert json.loads(io.dumps(io.from_dict(d))) == d


def test_known_keys_are_not_duplicated_via_extra():
    doc = io.load(UE_FULL)
    doc.extra["format"] = "Hacked"  # 既知のキーと衝突する extra は書き出しで無視される
    assert io.to_dict(doc)["format"] == "FacialCorrection"


# --- 欠けたキー・版・形式 ---


def test_missing_keys_become_defaults():
    doc = io.loads('{"format": "FacialCorrection", "version": 1}')
    assert isinstance(doc, m.Document)
    assert [l.name for l in doc.layers] == ["Neutral"]  # 最低でも Neutral
    assert (doc.grid.cols, doc.grid.rows, doc.grid.forward_axis, doc.grid.edge_fade) == (5, 3, "+X", 15)
    assert doc.policy.fade == (0, 0) and doc.policy.expression_dampen == 0.5 and doc.policy.snap_angle == 45
    assert doc.mirror.suffix_l == "_L" and doc.mirror.bone_axis == "Y" and doc.autogen.idw_power == 2
    assert (doc.meta.unit, doc.meta.up_axis, doc.meta.handedness) == ("cm", "Z", "left")
    assert doc.profile == "" and doc.asset is None
    # 欠けたキーは既定値で書き出される
    out = io.to_dict(doc)
    assert out["grid"]["cols"] == 5 and out["layers"][0]["name"] == "Neutral"


def test_missing_nested_keys_and_wrong_types_fall_back_to_defaults():
    doc = io.loads(
        json.dumps(
            {
                "format": "FacialCorrection",
                "grid": {"cols": "five", "yawRange": 120, "centerOffset": [1, 2]},
                "policy": {"fade": [5]},
                "layers": [{"name": "Neutral", "points": [{"row": 0, "col": 0, "curves": {"a": "x", "b": 1}, "bones": {"j": {"t": [1, 2, 3]}, "k": 3}}]}],
            }
        )
    )
    assert doc.version == 1
    assert doc.grid.cols == 5 and doc.grid.yaw_range == 120 and doc.grid.center_offset == (0, 0, 0)
    assert doc.policy.fade == (0, 0)
    p = doc.layers[0].points[(0, 0)]
    assert p.pose.curves == {"b": 1.0}
    assert list(p.pose.bones) == ["j"] and p.pose.bones["j"].r == (0, 0, 0, 1) and p.pose.bones["j"].s == (1, 1, 1)


def test_points_without_row_col_are_skipped_with_warning():
    with pytest.warns(UserWarning):
        doc = io.loads(json.dumps({"format": "FacialCorrection", "layers": [{"points": [{"isKey": True}, {"row": 1, "col": 1}]}]}))
    assert list(doc.layers[0].points) == [(1, 1)]


def test_points_outside_the_grid_are_kept():
    d = raw(UE_FULL)
    d["layers"][0]["points"].append({"row": 9, "col": 9, "isKey": True, "curves": {"x": 1}, "bones": {}})
    doc = io.from_dict(d)
    assert (9, 9) in doc.layers[0].points
    assert json.loads(io.dumps(doc)) == d or any(p["row"] == 9 for p in json.loads(io.dumps(doc))["layers"][0]["points"])


def test_newer_version_warns_and_still_reads():
    d = raw(UE_FULL)
    d["version"] = 7
    d["futureThing"] = 1
    with pytest.warns(io.FcposeVersionWarning):
        doc = io.from_dict(d)
    assert doc.version == 7 and doc.extra["futureThing"] == 1
    assert [l.name for l in doc.layers] == ["Neutral", "Joy"]
    pose = raw(UE_POSE)
    pose["version"] = 2
    with pytest.warns(io.FcposeVersionWarning):
        assert io.from_dict(pose).pose.curves["jaw_open"] > 0


def test_current_version_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        io.load(UE_FULL)
        io.load(UE_POSE)


def test_wrong_format_and_broken_json_raise():
    with pytest.raises(io.FcposeError):
        io.loads('{"format": "Something", "version": 1}')
    with pytest.raises(io.FcposeError):
        io.loads('{"version": 1}')
    with pytest.raises(io.FcposeError):
        io.loads("{ not json")
    with pytest.raises(io.FcposeError):
        io.loads("[1, 2]")
    with pytest.raises(io.FcposeError):
        io.load_document(UE_POSE)
    with pytest.raises(io.FcposeError):
        io.load_pose(UE_FULL)
    assert isinstance(io.load_document(UE_FULL), m.Document) and isinstance(io.load_pose(UE_POSE), m.PoseDocument)


def test_bom_is_accepted():
    text = "﻿" + UE_POSE.read_text(encoding="utf-8")
    assert isinstance(io.loads(text), m.PoseDocument)


# --- 書き出しの規則 ---


def test_only_present_points_are_written_row_major_and_empty_layers_ok():
    doc = io.load(UE_FULL)
    layer = doc.layers[0]
    del layer.points[(1, 0)]
    layer.points[(0, 0)] = m.GridPoint(0, 0, False, m.SourcePose({"x": 0.5}, {}))
    doc.layers[1].points.clear()
    out = io.to_dict(doc)
    assert [(p["row"], p["col"]) for p in out["layers"][0]["points"]] == [(0, 0), (0, 4), (1, 3), (1, 4), (2, 2)]
    assert out["layers"][1]["points"] == []


def test_quaternion_is_xyzw_and_not_renormalized():
    doc = m.PoseDocument(meta=m.Meta(), pose=m.SourcePose({}, {"j": m.BoneOffset((1, 2, 3), (0.1, 0.2, 0.3, 0.4), (1, 1, 1))}))
    out = json.loads(io.dumps(doc))
    assert out["bones"]["j"]["r"] == [0.1, 0.2, 0.3, 0.4]
    assert io.loads(io.dumps(doc)).pose.bones["j"].r == (0.1, 0.2, 0.3, 0.4)


def test_names_are_case_sensitive_and_unicode_is_kept():
    doc = m.PoseDocument(meta=m.Meta(), pose=m.SourcePose({"Jaw_Open": 1.0, "jaw_open": 0.5, "まばたき": 0.25}, {}))
    text = io.dumps(doc)
    assert "まばたき" in text  # \u エスケープにしない
    back = io.loads(text)
    assert back.pose.curves == {"Jaw_Open": 1.0, "jaw_open": 0.5, "まばたき": 0.25}


def test_number_formatting():
    doc = m.PoseDocument(meta=m.Meta(), pose=m.SourcePose({"a": 1.0, "b": 0.0, "c": -0.0, "d": 0.1, "e": 1e-9, "f": 1 / 3, "g": 123456789.0}, {}))
    text = io.dumps(doc)
    assert '"a": 1,' in text and '"b": 0,' in text and '"c": 0,' in text
    assert '"d": 0.1,' in text and '"e": 1e-09,' in text and '"g": 123456789\n' in text
    assert io.loads(text).pose.curves["f"] == 1 / 3  # 往復で桁が落ちない


def test_nan_and_inf_cannot_be_written():
    for bad in (float("nan"), float("inf")):
        doc = m.PoseDocument(meta=m.Meta(), pose=m.SourcePose({"a": bad}, {}))
        with pytest.raises(ValueError):
            io.dumps(doc)


def test_save_writes_utf8_lf_without_bom_and_creates_folders(tmp_path):
    target = tmp_path / "facial" / "shizuku" / "shizuku.fcpose.json"
    doc = io.load(TD_FULL)
    io.save(doc, target)
    data = target.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r" not in data and data.endswith(b"}\n")
    assert json.loads(data.decode("utf-8")) == raw(TD_FULL)
    assert io.dumps(io.load(target)) == data.decode("utf-8")


def test_dumps_is_independent_of_input_formatting():
    # UE 形式（タブ・1 要素 1 行）で読んでも、最小の 1 行 JSON から読んでも、出力は同じ
    d = raw(UE_FULL)
    assert io.dumps(io.loads(json.dumps(d))) == io.dumps(io.load(UE_FULL))
    assert io.dumps(io.loads(json.dumps(d, indent=4, sort_keys=False))) == io.dumps(io.load(UE_FULL))


def test_empty_containers_and_extra_values_are_written():
    doc = m.Document()
    doc.extra = {"emptyDict": {}, "emptyList": [], "nul": None, "flag": True, "n": 3}
    out = json.loads(io.dumps(doc))
    assert out["emptyDict"] == {} and out["emptyList"] == [] and out["nul"] is None and out["flag"] is True and out["n"] == 3


# --- スキーマ ---


def _resolve(root, ref):
    assert ref.startswith("#/")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _type_ok(value, t):
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "null":
        return value is None
    raise AssertionError(f"未対応の type: {t}")


def mini_validate(value, schema, root, path="$"):
    """この schema が使う範囲の JSON Schema だけを検査する最小の検査器（jsonschema が無い環境用）。"""
    errors = []
    if "$ref" in schema:
        return mini_validate(value, _resolve(root, schema["$ref"]), root, path)
    t = schema.get("type")
    if t is not None and not any(_type_ok(value, x) for x in (t if isinstance(t, list) else [t])):
        return [f"{path}: type {t} ではない ({type(value).__name__})"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const {schema['const']!r} ではない")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} は {schema['enum']} のどれでもない")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {value} <= exclusiveMinimum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: 要素が少ない")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: 要素が多い")
        if "items" in schema:
            for i, v in enumerate(value):
                errors += mini_validate(v, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errors.append(f"{path}: 必須キー {k} が無い")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errors += mini_validate(v, props[k], root, f"{path}.{k}")
            else:
                ap = schema.get("additionalProperties", True)
                if isinstance(ap, dict):
                    errors += mini_validate(v, ap, root, f"{path}.{k}")
                elif ap is False:
                    errors.append(f"{path}: 余計なキー {k}")
    for sub in schema.get("allOf", []):
        errors += mini_validate(value, sub, root, path)
    if "if" in schema:
        if not mini_validate(value, schema["if"], root, path):
            if "then" in schema:
                errors += mini_validate(value, schema["then"], root, path)
        elif "else" in schema:
            errors += mini_validate(value, schema["else"], root, path)
    return errors


def validate(instance):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = mini_validate(instance, schema, schema)
    try:
        import jsonschema  # 入っていれば本物の検査器でも確かめる（依存には加えない）
    except ImportError:
        return errors
    validator = jsonschema.Draft202012Validator(schema)
    return errors + [e.message for e in validator.iter_errors(instance)]


def test_schema_file_is_valid_json_with_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["$defs"]) >= {"vec3", "quat", "meta", "boneOffset", "point", "layer", "correction", "pose"}
    try:
        import jsonschema
    except ImportError:
        return
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_fixtures_validate_against_schema(path):
    assert validate(raw(path)) == []


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_written_output_validates_against_schema(path):
    assert validate(json.loads(io.dumps(io.load(path)))) == []


def test_schema_rejects_broken_documents():
    good = raw(UE_FULL)

    def broken(mutate):
        d = copy.deepcopy(good)
        mutate(d)
        return validate(d)

    assert broken(lambda d: d.pop("grid"))
    assert broken(lambda d: d.pop("meta"))
    assert broken(lambda d: d.update(format="Other"))
    assert broken(lambda d: d["grid"].update(cols="5"))
    assert broken(lambda d: d["grid"].update(forwardAxis="+W"))
    assert broken(lambda d: d["layers"][0]["points"][0].pop("row"))
    assert broken(lambda d: d["layers"][0]["points"][0]["curves"].update(x="1"))
    assert broken(lambda d: d["layers"][0]["points"][0]["bones"].update(j={"r": [0, 0, 1]}))
    assert broken(lambda d: d.update(layers=[]))
    assert broken(lambda d: d["meta"].update(upAxis="X"))
    pose = raw(UE_POSE)
    pose.pop("bones")
    assert validate(pose)


def test_schema_allows_unknown_keys_and_z_forward():
    d = raw(UE_FULL)
    d["whatever"] = {"x": 1}
    d["grid"]["gridExtra"] = 1
    d["grid"]["forwardAxis"] = "-Z"
    assert validate(d) == []
