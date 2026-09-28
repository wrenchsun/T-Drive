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
from pathlib import Path
from typing import Any

from . import params, roles

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
    }


def upgrade(look: dict[str, Any]) -> list[str]:
    """後から追加されたパラメータ（既定値で従来と同じ見た目になるもの）を base に補う。補った項目名を返す。

    追加のみの自動マイグレーション（docs/06 §2 の MINOR）。保存すると補った値もファイルに入る。
    """
    added = set()
    defaults = params.default_specific()
    for mat in look.get("materials", {}).values():
        spec = mat.setdefault("specific", {})
        for k, v in defaults.items():
            if k not in spec:
                spec[k] = copy.deepcopy(v)
                added.add(k)
    return sorted(added)


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
    return errors


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


def resolve(look: dict[str, Any], variant: str = BASE) -> dict[str, dict[str, Any]]:
    """variant の上書きを base に適用した、マテリアルごとの最終値を返す。"""
    result = copy.deepcopy(look.get("materials", {}))
    if variant == BASE:
        return result
    for mat_name, ov in look["variants"][variant].get("overrides", {}).items():
        mat = result[mat_name]
        mat["common"].update(copy.deepcopy(ov.get("common", {})))
        mat["specific"].update(copy.deepcopy(ov.get("specific", {})))
        if "renderQueueOffset" in ov:
            mat["renderQueueOffset"] = ov["renderQueueOffset"]
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
    ra, rb = resolve(look, a), resolve(look, b)
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
    look["materials"] = resolve(look, variant)
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
            if v is not None
        ],
        "RenderQueueOffset": mat["renderQueueOffset"],
        "RenderQueue": params.BASE_RENDER_QUEUE[c["blend"]] + mat["renderQueueOffset"],
        "SourceMaterial": f"Maya/{look['character']}/{material}",
    }
