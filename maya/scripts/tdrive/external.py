"""外部ツールパッケージ（docs/13 §6）。Maya 非依存（tools.json の読み書き・tool.json の検証・requires の判定）。

    %LOCALAPPDATA%\\TDriveToon\\tools.json          外部ツールの一覧（デザイナーごとの設定）
    %LOCALAPPDATA%\\TDriveToon\\tools\\<dir>\\       外部ツールの checkout（tool.json は直下）

- tools.json の壊れた部分（JSON・version・エントリ）は警告にして、壊れたエントリだけ落とす。他のツールは生かす
- `dir` が絶対パスなら開発用（自分の clone をそのまま読む。更新ボタンは無効）
- 戻り値の warnings は文字列のリスト。print はしない（表示は呼び出し側）
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .updater import fmt, parse_version

TOOLS_FILE = "tools.json"
TOOLS_DIR = "tools"
MANIFEST_FILE = "tool.json"
SCHEMA_VERSION = 1
DEFAULT_SCRIPTS = "maya/scripts"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ENTRY_RE = re.compile(r"^[A-Za-z_][\w.]*$")


# ---------------------------------------------------------------- 場所
def base_dir() -> Path:
    """%LOCALAPPDATA%\\TDriveToon（無ければ ~/AppData/Local/TDriveToon）。"""
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "TDriveToon"


def tools_json_path(base: str | Path | None = None) -> Path:
    """tools.json の場所。base は TDriveToon フォルダ（省略で既定）。"""
    return (Path(base) if base else base_dir()) / TOOLS_FILE


# ---------------------------------------------------------------- tools.json
@dataclass
class ToolEntry:
    """tools.json の 1 件。"""

    id: str
    url: str = ""
    dir: str = ""
    tag: str = ""
    previous: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.dir:
            self.dir = self.id

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "url": self.url, "dir": self.dir, "tag": self.tag}
        if self.previous:
            out["previous"] = self.previous
        out["enabled"] = self.enabled
        return out


def _parse_entry(raw: Any, index: int) -> tuple[ToolEntry | None, str | None]:
    where = f"tools[{index}]"
    if not isinstance(raw, dict):
        return None, f"{where}: オブジェクトではありません"
    tool_id = raw.get("id")
    if not isinstance(tool_id, str) or not _ID_RE.match(tool_id):
        return None, f"{where}: id が不正です（{tool_id!r}）"
    for key in ("url", "dir", "tag", "previous"):
        if key in raw and not isinstance(raw[key], str):
            return None, f"{where} ({tool_id}): {key} が文字列ではありません"
    if "enabled" in raw and not isinstance(raw["enabled"], bool):
        return None, f"{where} ({tool_id}): enabled が真偽値ではありません"
    return ToolEntry(
        id=tool_id,
        url=raw.get("url", ""),
        dir=raw.get("dir") or tool_id,
        tag=raw.get("tag", ""),
        previous=raw.get("previous", ""),
        enabled=raw.get("enabled", True),
    ), None


def load_tools(path: str | Path | None = None) -> tuple[list[ToolEntry], list[str]]:
    """tools.json を読む → (エントリ, 警告)。ファイルが無ければ空（警告なし）。壊れたエントリだけ落とす。"""
    p = Path(path) if path else tools_json_path()
    if not p.exists():
        return [], []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], [f"{p.name} を読めません: {e}"]
    if not isinstance(data, dict):
        return [], [f"{p.name}: 形式が不正です（オブジェクトではありません）"]
    if data.get("version") != SCHEMA_VERSION:
        return [], [f"{p.name}: version {data.get('version')!r} には対応していません（対応: {SCHEMA_VERSION}）"]
    raw_tools = data.get("tools", [])
    if not isinstance(raw_tools, list):
        return [], [f"{p.name}: tools が配列ではありません"]
    entries: list[ToolEntry] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_tools):
        entry, problem = _parse_entry(raw, i)
        if entry is None:
            warnings.append(f"{p.name} {problem}")
        elif entry.id in seen:
            warnings.append(f"{p.name} tools[{i}]: id {entry.id} が重複しています（先のものを使います）")
        else:
            seen.add(entry.id)
            entries.append(entry)
    return entries, warnings


def save_tools(entries: list[ToolEntry], path: str | Path | None = None) -> Path:
    p = Path(path) if path else tools_json_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": SCHEMA_VERSION, "tools": [e.to_json() for e in entries]}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def upsert_tool(entry: ToolEntry, path: str | Path | None = None) -> list[ToolEntry]:
    """id が同じものがあれば置き換え、無ければ追記して保存する。保存後の一覧を返す。"""
    entries, _ = load_tools(path)
    for i, e in enumerate(entries):
        if e.id == entry.id:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    save_tools(entries, path)
    return entries


def remove_tool(tool_id: str, path: str | Path | None = None) -> bool:
    """id のエントリを外して保存する（checkout は消さない）。あったか。"""
    entries, _ = load_tools(path)
    kept = [e for e in entries if e.id != tool_id]
    if len(kept) == len(entries):
        return False
    save_tools(kept, path)
    return True


# ---------------------------------------------------------------- checkout の場所
def is_dev_dir(dir_value: str) -> bool:
    """dir が絶対パスなら開発用（自分の clone をそのまま読む。更新ボタン無効）。"""
    return bool(dir_value) and (Path(dir_value).is_absolute() or bool(re.match(r"^[A-Za-z]:[\\/]", dir_value)))


def checkout_path(entry: ToolEntry, base: str | Path | None = None) -> Path:
    """checkout の実パス。dir が相対なら <base>/tools/<dir>、絶対ならそのまま。"""
    if is_dev_dir(entry.dir):
        return Path(entry.dir)
    return (Path(base) if base else base_dir()) / TOOLS_DIR / entry.dir


# ---------------------------------------------------------------- tool.json
@dataclass
class ToolManifest:
    """tool.json（外部ツールの checkout 直下。docs/13 §6.2）。"""

    id: str
    name: str
    entry: str
    scripts: str = DEFAULT_SCRIPTS
    maya_version: str = ""
    requires_tdrive: str = ""
    compatible_with: dict[str, str] = field(default_factory=dict)  # 表示だけ


def parse_manifest(data: Any) -> tuple[ToolManifest | None, str]:
    """tool.json の中身を検証する → (ToolManifest, "") または (None, 理由)。"""
    if not isinstance(data, dict):
        return None, "tool.json の形式が不正です（オブジェクトではありません）"
    tool_id = data.get("id")
    if not isinstance(tool_id, str) or not _ID_RE.match(tool_id):
        return None, f"tool.json の id が不正です（{tool_id!r}。小文字英字で始まる英小文字・数字・_）"
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "tool.json の name がありません"
    entry = data.get("entry")
    if not isinstance(entry, str) or not _ENTRY_RE.match(entry):
        return None, f"tool.json の entry が不正です（{entry!r}。モジュールパス）"
    scripts = data.get("scripts", DEFAULT_SCRIPTS)
    if not isinstance(scripts, str) or not scripts.strip():
        return None, "tool.json の scripts が不正です"
    maya_version = data.get("mayaVersion", "")
    if not isinstance(maya_version, str):
        return None, "tool.json の mayaVersion が文字列ではありません"
    requires_tdrive = ""
    requires = data.get("requires", {})
    if not isinstance(requires, dict):
        return None, "tool.json の requires がオブジェクトではありません"
    if "tdrive" in requires:
        requires_tdrive = requires["tdrive"]
        if not isinstance(requires_tdrive, str) or parse_version(requires_tdrive) is None:
            return None, f"tool.json の requires.tdrive が不正です（{requires_tdrive!r}。X.Y.Z）"
    compatible = data.get("compatibleWith", {})
    if not isinstance(compatible, dict):
        return None, "tool.json の compatibleWith がオブジェクトではありません"
    return ToolManifest(
        id=tool_id,
        name=name.strip(),
        entry=entry,
        scripts=scripts,
        maya_version=maya_version,
        requires_tdrive=requires_tdrive,
        compatible_with={str(k): str(v) for k, v in compatible.items()},
    ), ""


def read_manifest(checkout: str | Path) -> tuple[ToolManifest | None, str]:
    """checkout 直下の tool.json を読んで検証する → (ToolManifest, "") または (None, 理由)。"""
    p = Path(checkout) / MANIFEST_FILE
    if not p.is_file():
        return None, f"{MANIFEST_FILE} がありません（{Path(checkout)}）"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{MANIFEST_FILE} を読めません: {e}"
    return parse_manifest(data)


def check_requires(manifest: ToolManifest, tdrive_version: str | None = None) -> str:
    """requires.tdrive を本体の版と比べる。満たせば ""、満たさなければ読み込まない理由。"""
    if not manifest.requires_tdrive:
        return ""
    need = parse_version(manifest.requires_tdrive)
    have = parse_version(tdrive_version if tdrive_version is not None else __version__)
    if need is None:
        return f"requires.tdrive が不正です（{manifest.requires_tdrive!r}）"
    if have is None:
        return ""  # 本体の版が読めないとき（開発用）は判定しない
    if have < need:
        return f"T-Drive {fmt(need)} 以上が必要です（今は {fmt(have)}）。T-Drive を更新してください"
    return ""


# ---------------------------------------------------------------- まとめて解決
@dataclass
class LoadableTool:
    """読み込めるツール（R-9 の起動処理が scripts_path を sys.path に足して manifest.entry を import する）。"""

    entry: ToolEntry
    manifest: ToolManifest
    checkout: Path
    scripts_path: Path  # scripts の絶対パス
    dev: bool


@dataclass
class SkippedTool:
    """読み込まないツールとその理由。"""

    id: str
    reason: str


def resolve_tools(
    path: str | Path | None = None,
    tdrive_version: str | None = None,
) -> tuple[list[LoadableTool], list[SkippedTool], list[str]]:
    """tools.json を読み、各エントリの checkout・tool.json・requires を調べる → (読み込める, 読み込まない, 警告)。

    path は tools.json の場所（既定 %LOCALAPPDATA%\\TDriveToon\\tools.json）。相対 dir の checkout は
    その tools.json と同じフォルダの tools/<dir>。
    """
    p = Path(path) if path else tools_json_path()
    entries, warnings = load_tools(p)
    loadable: list[LoadableTool] = []
    skipped: list[SkippedTool] = []
    for e in entries:
        if not e.enabled:
            skipped.append(SkippedTool(e.id, "無効"))
            continue
        checkout = checkout_path(e, p.parent)
        if not checkout.is_dir():
            skipped.append(SkippedTool(e.id, f"checkout が見つかりません（{checkout}）"))
            continue
        manifest, reason = read_manifest(checkout)
        if manifest is None:
            skipped.append(SkippedTool(e.id, reason))
            continue
        if manifest.id != e.id:
            skipped.append(SkippedTool(e.id, f"tool.json の id（{manifest.id}）が tools.json の id と違います"))
            continue
        reason = check_requires(manifest, tdrive_version)
        if reason:
            skipped.append(SkippedTool(e.id, reason))
            continue
        scripts = Path(manifest.scripts)
        scripts_path = scripts if scripts.is_absolute() else checkout / scripts
        loadable.append(LoadableTool(e, manifest, checkout, scripts_path, is_dev_dir(e.dir)))
    return loadable, skipped, warnings
