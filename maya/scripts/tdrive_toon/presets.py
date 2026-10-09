"""名前付きプリセット（docs/05 §4.2 E-5。Maya / Qt 非依存）。

保存先は `<プロジェクト>/presets/<名前>.json`。形:
    {"schemaVersion": 1, "label": "...", "groups": ["Shadow", "Outline"],
     "specific": {...}, "common": {...}, "renderQueueOffset": n}      ← 含めるグループのキーだけ
ロール（roles.py）のプリセットとは別物。Look には何も残さず、適用した値だけが Look に入る。
読み込み時は契約（look.filter_pasteable）で検証し、知らないキー・型違いは捨てる（警告だけで落ちない）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import look, look_layout, project

SCHEMA_VERSION = 1
FOLDER = "presets"
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class PresetError(ValueError):
    """読めないプリセット（壊れた JSON・形が違う・新しすぎる版）。"""


@dataclass
class Preset:
    label: str
    groups: list[str]
    flat: dict[str, Any]  # 検証済みの値（平らな辞書）
    dropped: list[str] = field(default_factory=list)  # 捨てたキー
    warnings: list[str] = field(default_factory=list)


@dataclass
class Entry:
    name: str
    path: Path
    label: str
    ok: bool
    error: str = ""


def presets_dir(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else project.root()) / FOLDER


def safe_name(name: str) -> str:
    """ファイル名にできる名前に整える。使えなければ ValueError。"""
    n = (name or "").strip()
    if n.lower().endswith(".json"):
        n = n[:-5].strip()
    if not n:
        raise ValueError("名前を入力してください")
    if _BAD_NAME.search(n) or n.startswith(".") or n.endswith(".") or len(n) > 80:
        raise ValueError('名前に使えない文字があります（\\ / : * ? " < > | と、先頭・末尾の . は使えません。80 文字まで）')
    return n


def path_of(name: str, root: Path | None = None) -> Path:
    return presets_dir(root) / f"{safe_name(name)}.json"


def build(label: str, groups: list[str], flat: dict[str, Any]) -> dict[str, Any]:
    """保存する JSON を作る。groups に属するキーだけ入れる（知らないグループは捨てる）。"""
    known = look_layout.build_groups()
    groups = [g for g in groups if g in known]
    keys = set(look_layout.keys_of_groups(known, groups))
    data: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION, "label": label, "groups": groups}
    data.update(look.flat_to_payload({k: v for k, v in flat.items() if k in keys}))
    return data


def save(name: str, data: dict[str, Any], root: Path | None = None) -> Path:
    path = path_of(name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def parse(data: Any) -> Preset:
    if not isinstance(data, dict):
        raise PresetError("プリセットの形ではありません（{ … } が必要）")
    ver = data.get("schemaVersion")
    if not isinstance(ver, int) or isinstance(ver, bool):
        raise PresetError("schemaVersion がありません")
    if ver > SCHEMA_VERSION:
        raise PresetError(f"新しい版のプリセットです（schemaVersion {ver}。このツールは {SCHEMA_VERSION} まで）")
    warnings: list[str] = []
    known = look_layout.build_groups()
    groups = data.get("groups", [])
    if not isinstance(groups, list):
        warnings.append("groups が一覧ではありません")
        groups = []
    unknown = [g for g in groups if not isinstance(g, str) or g not in known]
    if unknown:
        warnings.append(f"知らないグループを無視: {', '.join(map(str, unknown))}")
    groups = [g for g in groups if isinstance(g, str) and g in known]
    body = {k: data[k] for k in ("specific", "common", "renderQueueOffset") if k in data}
    flat, dropped = look.filter_pasteable(body)
    if dropped:
        warnings.append(f"捨てた項目: {', '.join(dropped)}")
    label = data.get("label")
    return Preset(label if isinstance(label, str) else "", groups, flat, dropped, warnings)


def load(path: Path) -> Preset:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # UnicodeDecodeError / JSONDecodeError は ValueError
        raise PresetError(f"読めません: {exc}") from exc
    return parse(data)


def list_presets(root: Path | None = None) -> list[Entry]:
    """プリセットの一覧（名前順）。壊れたものも ok=False で返す（メニューに「読めません」と出すため）。"""
    d = presets_dir(root)
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            p = load(path)
            out.append(Entry(path.stem, path, p.label or path.stem, True))
        except PresetError as exc:
            out.append(Entry(path.stem, path, path.stem, False, str(exc)))
    return out
