"""改名を Look の辞書に追従させる（docs/20 §2.5「改名計画」。Maya 非依存）。

マテリアル名・メッシュ名が変わっても Look が壊れないようにする。**割り当てそのものは変えない**（名前を書き換えるだけ）。

- マテリアル: `materials` のキー、`parts[*].materials`、`variants[*].overrides` のキー
- メッシュ: `characterSettings.viewCorrection` のメッシュ名（`mesh` ほか）
"""

from __future__ import annotations

from typing import Any


def rename_materials(look: dict[str, Any], mapping: dict[str, str]) -> int:
    """マテリアル名を置き換える。書き換えた箇所の数を返す。新しい名前が既に Look にあれば何もしない（上書きしない）。"""
    mapping = {a: b for a, b in mapping.items() if a != b and a in look.get("materials", {}) and b not in look.get("materials", {})}
    if not mapping:
        return 0
    n = 0
    look["materials"] = {mapping.get(k, k): v for k, v in look["materials"].items()}
    n += len(mapping)
    for part in look.get("parts", {}).values():
        mats = part.get("materials", [])
        if any(m in mapping for m in mats):
            part["materials"] = [mapping.get(m, m) for m in mats]
            n += 1
    for var in look.get("variants", {}).values():
        ov = var.get("overrides", {})
        if any(k in mapping for k in ov):
            var["overrides"] = {mapping.get(k, k): v for k, v in ov.items()}
            n += 1
    return n


def rename_meshes(look: dict[str, Any], mapping: dict[str, str]) -> int:
    vc = look.get("characterSettings", {}).get("viewCorrection", {})
    n = 0
    for k, v in list(vc.items()):
        if isinstance(v, str) and v in mapping:
            vc[k] = mapping[v]
            n += 1
    return n


def references(look: dict[str, Any], materials: set[str]) -> list[str]:
    """Look が参照している（旧）マテリアル名のうち、materials に含まれるもの。"""
    return sorted(m for m in look.get("materials", {}) if m in materials)
