"""Look 定義（キャラクターのルックの唯一の真実）の読み書き・編集・検証（Maya 非依存）。

構造は D-Drive MaterialData と 1 対 1 に揃えてある:
  materials.<name>.common            -> MaterialData.Common
  materials.<name>.specific          -> MaterialData.Specific[]
  materials.<name>.renderQueueOffset -> MaterialData.RenderQueueOffset
詳細仕様: docs/02_look_definition_spec.md
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from . import features, params, roles

SCHEMA_VERSION = 1
BASE = "base"


# ---------------------------------------------------------------- 生成 / 入出力


def new_look(character: str, model: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "character": character,
        "model": model,
        "lookVersion": "0.1.0",
        "parts": {},
        "materials": {},
        "variants": {},
        SETTINGS: copy.deepcopy(CHARACTER_DEFAULTS),
        FEATURES: features.default_flags(),
    }


# キャラクター単位の設定の既定値（docs/02 §4.1）
CHARACTER_DEFAULTS: dict[str, Any] = {
    "light": {"smoothing": 0.15, "hysteresisDeg": 3.0},
    "stencil": {"enabled": False},
    "innerLine": {"enabled": False, "width": 1.0, "color": [0.2, 0.15, 0.15, 1.0], "parts": []},
    "screenOutline": {"enabled": False, "width": 2.0, "color": [0.2, 0.15, 0.15, 1.0]},
    "contactShadow": {"enabled": False, "radius": 0.25, "strength": 0.5},
    "viewCorrection": {"mesh": "", "front": "", "threeQuarter": "", "side": ""},
    "depthCompression": 0.0,
    "faceShadow": {"forward": [0.0, 0.0, 1.0], "right": [-1.0, 0.0, 0.0]},
    "expressions": {},
}
EXPRESSION_NAME = re.compile(r"^[A-Za-z0-9_]+$")
SETTINGS = "characterSettings"  # トップレベルの "character" はキャラクター ID
FEATURES = "features"  # 機能のオン/オフ（docs/11）


def _fill_defaults(target: dict[str, Any], defaults: dict[str, Any], prefix: str, added: set[str]) -> None:
    for k, v in defaults.items():
        if k not in target:
            target[k] = copy.deepcopy(v)
            added.add(f"{prefix}{k}")
        elif isinstance(v, dict) and isinstance(target[k], dict) and k != "expressions":
            _fill_defaults(target[k], v, f"{prefix}{k}.", added)


def upgrade(look: dict[str, Any]) -> list[str]:
    """後から追加されたパラメータ（既定値で従来と同じ見た目になるもの）を base に補う。補った項目名を返す。

    追加のみの自動マイグレーション（docs/06 §2 の MINOR）。保存すると補った値もファイルに入る。
    """
    added = set()
    _fill_defaults(look.setdefault(SETTINGS, {}), CHARACTER_DEFAULTS, f"{SETTINGS}.", added)
    defaults = params.default_specific()
    for mat in look.get("materials", {}).values():
        spec = mat.setdefault("specific", {})
        for k, v in defaults.items():
            if k not in spec:
                spec[k] = copy.deepcopy(v)
                added.add(k)
    # 機能のオン/オフ: 無い機能は「既定でオン」か「値が既定と違う（= 使われている）」ならオン（見た目を変えない）
    flags = look.setdefault(FEATURES, {})
    used = used_features(look)
    for f in features.FEATURES:
        if f.id not in flags:
            flags[f.id] = f.default_on or f.id in used
            added.add(f"{FEATURES}.{f.id}")
    return sorted(added)


def used_features(look: dict[str, Any]) -> set[str]:
    """値が効果なしの値と違う（= 使われている）機能。"""
    used = set()
    pdefaults = {p.unity: p.default for p in params.SPECIFIC_PARAMS}
    sections = [m.get("specific", {}) for m in look.get("materials", {}).values()]
    sections += [ov.get("specific", {}) for v in look.get("variants", {}).values() for ov in v.get("overrides", {}).values()]
    for spec in sections:
        for k, v in spec.items():
            if k in features.FEATURE_OF_PARAM and v != features.off_value(k, pdefaults.get(k)):
                used.add(features.FEATURE_OF_PARAM[k])
    for key, v in look.get(SETTINGS, {}).items():
        if key in features.FEATURE_OF_SETTING and v != CHARACTER_DEFAULTS.get(key):
            used.add(features.FEATURE_OF_SETTING[key])
    return used


def enabled(look: dict[str, Any], feature_id: str) -> bool:
    f = features.BY_ID[feature_id]
    return f.required or bool(look.get(FEATURES, {}).get(feature_id, f.default_on))


def enabled_features(look: dict[str, Any]) -> list[str]:
    """オンの機能 ID（定義順ではなく名前順。出力・生成シェーダーのキーに使う）。"""
    return sorted(f.id for f in features.FEATURES if enabled(look, f.id))


def line_part_keys(look: dict[str, Any]) -> dict[str, float]:
    """マテリアル → ToonId の部位キー（docs/03 §10.1）。部位番号 = 部位名の昇順で 1 始まり、線フラグ = innerLine.parts に含まれる。

    インナーラインがオフ（機能・設定）のときは線フラグがすべて 0。部位に属さないマテリアルは含めない（= 0: ToonId を書かない）。
    """
    il = resolved_settings(look)["innerLine"]
    line_parts = set(il["parts"]) if il["enabled"] else set()
    keys = {}
    for i, part in enumerate(sorted(look.get("parts", {})), start=1):
        for mat in look["parts"][part]["materials"]:
            keys[mat] = float(i * 2 + (1 if part in line_parts else 0))
    return keys


def set_feature(look: dict[str, Any], feature_id: str, on: bool) -> None:
    f = features.BY_ID[feature_id]
    if f.required and not on:
        raise ValueError(f"{f.label} は必須の機能なのでオフにできません")
    look.setdefault(FEATURES, features.default_flags())[feature_id] = bool(on)


def resolved_settings(look: dict[str, Any]) -> dict[str, Any]:
    """characterSettings の最終値。オフの機能の設定は既定値（= 効果なし）にする。"""
    cs = copy.deepcopy(look.get(SETTINGS, CHARACTER_DEFAULTS))
    for key, fid in features.FEATURE_OF_SETTING.items():
        if not enabled(look, fid):
            cs[key] = copy.deepcopy(CHARACTER_DEFAULTS[key])
    return cs


def load(path: str | Path) -> dict[str, Any]:
    look = json.loads(Path(path).read_text(encoding="utf-8"))
    upgrade(look)
    errors = validate(look)
    if errors:
        raise ValueError(f"Look 定義が不正です ({path}):\n  " + "\n  ".join(errors))
    return look


def dumps(look: dict[str, Any]) -> str:
    # キー順を固定して差分レビューしやすくする
    return json.dumps(look, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save(look: dict[str, Any], path: str | Path) -> None:
    errors = validate(look)
    if errors:
        raise ValueError("Look 定義が不正なため保存しません:\n  " + "\n  ".join(errors))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(look), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- 検証


def validate(look: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if look.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"schemaVersion は {SCHEMA_VERSION} のみ対応 (実際: {look.get('schemaVersion')})")
    for key in ("character", "model", "lookVersion"):
        if not isinstance(look.get(key), str) or not look.get(key):
            errors.append(f"{key} が必要")
    mats = look.get("materials", {})
    for part_name, part in look.get("parts", {}).items():
        if part.get("role") not in roles.ROLES:
            errors.append(f"parts.{part_name}.role が不明: {part.get('role')}")
        for m in part.get("materials", []):
            if m not in mats:
                errors.append(f"parts.{part_name} が未定義のマテリアル {m} を参照")
    owners: dict[str, str] = {}
    for part_name, part in look.get("parts", {}).items():
        for m in part.get("materials", []):
            if m in owners:
                errors.append(f"マテリアル {m} が複数の部位 ({owners[m]}, {part_name}) に登録されている")
            owners[m] = part_name
    for mat_name, mat in mats.items():
        errors += _validate_material(f"materials.{mat_name}", mat, partial=False)
    for var_name, var in look.get("variants", {}).items():
        if var_name == BASE:
            errors.append(f"variant 名 '{BASE}' は予約語")
        for mat_name, ov in var.get("overrides", {}).items():
            if mat_name not in mats:
                errors.append(f"variants.{var_name} が未定義のマテリアル {mat_name} を上書き")
            errors += _validate_material(f"variants.{var_name}.{mat_name}", ov, partial=True)
    if SETTINGS in look:
        errors += _validate_settings(look)
    for fid, on in look.get(FEATURES, {}).items():
        if fid not in features.BY_ID:
            errors.append(f"{FEATURES}.{fid} は未知の機能")
        elif not isinstance(on, bool):
            errors.append(f"{FEATURES}.{fid} は true / false")
        elif features.BY_ID[fid].required and not on:
            errors.append(f"{FEATURES}.{fid} は必須の機能なのでオフにできない")
    return errors


def _validate_settings(look: dict[str, Any]) -> list[str]:
    """characterSettings の検証（docs/02 §4.1）。"""
    cs = look[SETTINGS]
    w = SETTINGS
    errors = []

    def num(path: str, v: Any, lo: float | None = None, hi: float | None = None) -> None:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errors.append(f"{w}.{path} は数値")
        elif (lo is not None and v < lo) or (hi is not None and v > hi):
            errors.append(f"{w}.{path} は {lo}〜{hi} の範囲")

    light = cs.get("light", {})
    num("light.smoothing", light.get("smoothing", 0), 0, None)
    num("light.hysteresisDeg", light.get("hysteresisDeg", 0), 0, None)
    il = cs.get("innerLine", {})
    num("innerLine.width", il.get("width", 1), 0, None)
    color = il.get("color", [0, 0, 0, 1])
    if not (isinstance(color, list) and len(color) == 4):
        errors.append(f"{w}.innerLine.color は [r, g, b, a]")
    for part in il.get("parts", []):
        if part not in look.get("parts", {}):
            errors.append(f"{w}.innerLine.parts に未登録の部位 {part}")
    so = cs.get("screenOutline", {})
    num("screenOutline.width", so.get("width", 2), 0, None)
    if not (isinstance(so.get("color", [0, 0, 0, 1]), list) and len(so.get("color", [0, 0, 0, 1])) == 4):
        errors.append(f"{w}.screenOutline.color は [r, g, b, a]")
    cs_shadow = cs.get("contactShadow", {})
    num("contactShadow.radius", cs_shadow.get("radius", 0.25), 0, None)
    num("contactShadow.strength", cs_shadow.get("strength", 0.5), 0, 1)
    num("depthCompression", cs.get("depthCompression", 0), 0, 1)
    for key in ("forward", "right"):
        v = cs.get("faceShadow", {}).get(key, [0, 0, 1])
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(c, (int, float)) for c in v)) or (v[0] ** 2 + v[2] ** 2) < 1e-8:
            errors.append(f"{w}.faceShadow.{key} は水平成分のある [x, y, z]")
    for key, v in cs.get("viewCorrection", {}).items():
        if not isinstance(v, str):
            errors.append(f"{w}.viewCorrection.{key} は文字列（BlendShape 名）")
    for name, entries in cs.get("expressions", {}).items():
        if not EXPRESSION_NAME.match(name):
            errors.append(f"{w}.expressions の名前 {name} は英数字と _ のみ")
        for i, e in enumerate(entries):
            where = f"{w}.expressions.{name}[{i}]"
            if e.get("material") not in look.get("materials", {}):
                errors.append(f"{where}.material が未登録: {e.get('material')}")
            p = params.PARAMS_BY_UNITY.get(e.get("property"))
            if p is None or p.kind != params.FLOAT:
                errors.append(f"{where}.property は Float のパラメータ契約（_Toon*）: {e.get('property')}")
            num(f"expressions.{name}[{i}].min", e.get("min", 0))
            num(f"expressions.{name}[{i}].max", e.get("max", 1))
    return errors


def get_setting(look: dict[str, Any], path: str) -> Any:
    """characterSettings の値を "innerLine.width" のようなパスで取る。"""
    node: Any = look.get(SETTINGS, {})
    for key in path.split("."):
        node = node[key]
    return node


def set_setting(look: dict[str, Any], path: str, value: Any) -> None:
    node = look.setdefault(SETTINGS, copy.deepcopy(CHARACTER_DEFAULTS))
    keys = path.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = copy.deepcopy(value)


def expression_values(look: dict[str, Any], name: str, t: float) -> list[tuple[str, str, float]]:
    """表情パラメータ name を t（0–1）にしたときの (material, property, 値) の一覧（T-25）。"""
    out = []
    for e in look.get(SETTINGS, {}).get("expressions", {}).get(name, []):
        out.append((e["material"], e["property"], e["min"] + (e["max"] - e["min"]) * t))
    return out


def _validate_material(where: str, mat: dict[str, Any], partial: bool) -> list[str]:
    errors = []
    common = mat.get("common", {})
    for k, v in common.items():
        if k not in params.COMMON_FIELDS:
            errors.append(f"{where}.common.{k} は D-Drive MaterialCommon に存在しない")
        elif k == "blend" and v not in params.BLEND_TYPES:
            errors.append(f"{where}.common.blend は {params.BLEND_TYPES} のいずれか")
    for k, v in mat.get("specific", {}).items():
        p = params.PARAMS_BY_UNITY.get(k)
        if p is None:
            errors.append(f"{where}.specific.{k} はパラメータ契約 (params.py) に無い")
            continue
        err = params.validate_value(p, v)
        if err:
            errors.append(f"{where}.specific.{err}")
    if not partial and not isinstance(mat.get("renderQueueOffset", 0), int):
        errors.append(f"{where}.renderQueueOffset は整数")
    return errors


# ---------------------------------------------------------------- 部位登録


def ensure_material(look: dict[str, Any], name: str) -> dict[str, Any]:
    mats = look.setdefault("materials", {})
    if name not in mats:
        mats[name] = {
            "shader": params.UNITY_SHADER,
            "common": copy.deepcopy(params.COMMON_FIELDS),
            "specific": params.default_specific(),
            "renderQueueOffset": 0,
        }
    return mats[name]


def register_part(
    look: dict[str, Any], part: str, role: str, materials: list[str], apply_preset: bool = True
) -> None:
    """部位を登録（既存なら置き換え）し、ロールのプリセットを各マテリアルの base 値に流す。

    他の部位に登録済みのマテリアルは、そちらから外してこの部位へ移す。
    """
    if role not in roles.ROLES:
        raise ValueError(f"不明なロール: {role}")
    for other_name, other in look.setdefault("parts", {}).items():
        if other_name != part:
            other["materials"] = [m for m in other["materials"] if m not in materials]
    look["parts"][part] = {"role": role, "materials": list(materials)}
    for m in materials:
        mat = ensure_material(look, m)
        if apply_preset:
            preset = roles.ROLE_PRESETS[role]
            mat["common"].update(copy.deepcopy(preset.get("common", {})))
            mat["specific"].update(copy.deepcopy(preset.get("specific", {})))
            mat["renderQueueOffset"] = preset.get("renderQueueOffset", 0)
    # 空になった部位は消す
    look["parts"] = {k: v for k, v in look["parts"].items() if v["materials"] or k == part}


def part_of(look: dict[str, Any], material: str) -> str | None:
    for name, part in look.get("parts", {}).items():
        if material in part["materials"]:
            return name
    return None


# ---------------------------------------------------------------- バリアント（A/B）


def variant_names(look: dict[str, Any]) -> list[str]:
    return [BASE, *sorted(look.get("variants", {}))]


def add_variant(look: dict[str, Any], name: str, label: str = "", copy_from: str = BASE) -> None:
    if name == BASE or name in look.get("variants", {}):
        raise ValueError(f"variant '{name}' は作成できない（予約語または既存）")
    overrides = {}
    if copy_from != BASE:
        overrides = copy.deepcopy(look["variants"][copy_from]["overrides"])
    look.setdefault("variants", {})[name] = {"label": label or name, "overrides": overrides}


def merged(look: dict[str, Any], variant: str = BASE) -> dict[str, dict[str, Any]]:
    """variant の上書きを base に合成しただけの値（機能のオン/オフは適用しない。保存・採用に使う）。"""
    result = copy.deepcopy(look.get("materials", {}))
    if variant != BASE:
        for mat_name, ov in look["variants"][variant].get("overrides", {}).items():
            mat = result[mat_name]
            mat["common"].update(copy.deepcopy(ov.get("common", {})))
            mat["specific"].update(copy.deepcopy(ov.get("specific", {})))
            if "renderQueueOffset" in ov:
                mat["renderQueueOffset"] = ov["renderQueueOffset"]
    return result


def resolve(look: dict[str, Any], variant: str = BASE) -> dict[str, dict[str, Any]]:
    """variant の上書きを base に適用した、マテリアルごとの最終値を返す。"""
    result = merged(look, variant)
    # オフの機能のパラメータは「効果なしの値」で解決する（保存値は消さない。docs/11 §2）
    pdefaults = {p.unity: p.default for p in params.SPECIFIC_PARAMS}
    on = enabled_features(look)
    for mat in result.values():
        for k in list(mat["specific"]):
            fid = features.FEATURE_OF_PARAM.get(k)
            if fid and fid not in on:
                mat["specific"][k] = copy.deepcopy(features.off_value(k, pdefaults.get(k)))
        mat["features"] = on  # 解決結果だけに付ける（プレビュー・出力が使う。保存データには入らない）
    return result


def set_value(look: dict[str, Any], material: str, key: str, value: Any, variant: str = BASE) -> None:
    """key は Specific のプロパティ名 (_Toon...)、"common.<field>"、"renderQueueOffset" のいずれか。"""
    if variant == BASE:
        target = look["materials"][material]
    else:
        target = look["variants"][variant]["overrides"].setdefault(material, {})
    value = copy.deepcopy(value)
    if key == "renderQueueOffset":
        target["renderQueueOffset"] = int(value)
    elif key.startswith("common."):
        target.setdefault("common", {})[key.split(".", 1)[1]] = value
    else:
        if key not in params.PARAMS_BY_UNITY:
            raise KeyError(f"パラメータ契約に無い: {key}")
        target.setdefault("specific", {})[key] = value


def diff(look: dict[str, Any], a: str, b: str) -> list[tuple[str, str, Any, Any]]:
    """2 つのバリアントで値が異なる (material, key, a値, b値) の一覧。"""
    ra, rb = merged(look, a), merged(look, b)  # 差分は保存されている値で見る（オフの機能も含めて）
    rows = []
    for mat in sorted(ra):
        for section in ("common", "specific"):
            keys = sorted(set(ra[mat][section]) | set(rb[mat][section]))
            for k in keys:
                va, vb = ra[mat][section].get(k), rb[mat][section].get(k)
                if va != vb:
                    rows.append((mat, k if section == "specific" else f"common.{k}", va, vb))
        if ra[mat]["renderQueueOffset"] != rb[mat]["renderQueueOffset"]:
            rows.append((mat, "renderQueueOffset", ra[mat]["renderQueueOffset"], rb[mat]["renderQueueOffset"]))
    return rows


def promote(look: dict[str, Any], variant: str) -> None:
    """variant の内容を base に確定し、variant を削除する（B 案採用）。"""
    look["materials"] = merged(look, variant)  # resolve だとオフの機能の値・features が焼き込まれる
    del look["variants"][variant]


# ---------------------------------------------------------------- D-Drive 向け出力


def _param_value(value: Any) -> dict[str, Any]:
    """D-Drive ParamValue 相当の表現。"""
    if isinstance(value, bool):
        return {"Type": "Bool", "BoolValue": value}
    if isinstance(value, (int, float)):
        return {"Type": "Float", "FloatValue": float(value)}
    if isinstance(value, list):
        return {"Type": "Color", "ColorValue": value}
    return {"Type": "Object", "ObjectPath": value}


def to_ddrive_material_data(look: dict[str, Any], material: str, variant: str = BASE) -> dict[str, Any]:
    """Unity 側インポーター (MS2026) が MaterialData を生成するための中間表現。"""
    mat = resolve(look, variant)[material]
    c = mat["common"]
    return {
        "Shader": mat.get("shader", params.UNITY_SHADER),
        "Common": {
            "Albedo": c["albedo"],
            "AlbedoTint": c["albedoTint"],
            "Normal": c["normal"],
            "NormalScale": c["normalScale"],
            "Emission": c["emission"],
            "EmissionColor": c["emissionColor"],
            "EmissionIntensity": c["emissionIntensity"],
            "Blend": c["blend"],
            "Cutoff": c["cutoff"],
            "DoubleSided": c["doubleSided"],
        },
        "Specific": [
            {"Property": k, "Value": _param_value(v)}
            for k, v in sorted(mat["specific"].items())
            if v is not None and features.FEATURE_OF_PARAM.get(k) in mat["features"]
        ],
        "RenderQueueOffset": mat["renderQueueOffset"],
        "RenderQueue": params.BASE_RENDER_QUEUE[c["blend"]] + mat["renderQueueOffset"],
        "SourceMaterial": f"Maya/{look['character']}/{material}",
    }
