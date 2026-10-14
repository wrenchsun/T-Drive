"""外部ツールの読み込み（docs/13 §6）。起動時（userSetup）とリロード（reload_tdrive.py）から呼ぶ。

    resolve_tools()  →  scripts_path を sys.path に足す  →  import manifest.entry  →  create_tool()  →  shell.register_tool

- Maya 非依存（shell は関数の中で遅延 import する。テストは register / unavailable を差し替えて検査する）
- 1 つの外部ツールの失敗（import 例外・create_tool が無い / 例外 / id の食い違い）は「読み込めなかったタブ」にして、
  Toon / FacialController と他の外部ツールを止めない
- 「無効」（tools.json の enabled=false）はタブを出さない（ログだけ）。そのほかの読み込まない理由はタブに出す
- リロード: 外部ツールのモジュール（scripts_path 配下）を sys.modules から外し、sys.path からも外す（`unload`）。
  `lifecycle.on_reload` に登録してあるので、reload_tdrive.py の後片付けで自動的に呼ばれる
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable

from . import external, lifecycle

_scripts_paths: list[str] = []  # 今回の読み込みで sys.path に足した（または使った）scripts のパス
_added_to_path: list[str] = []  # そのうち、こちらが足したもの（unload で外す）


def _log(message: str) -> None:
    print(f"[T-Drive] {message}")


def _add_path(scripts_path: Path) -> None:
    p = str(scripts_path)
    if p not in _scripts_paths:
        _scripts_paths.append(p)
    if p not in sys.path:
        sys.path.insert(0, p)
        _added_to_path.append(p)


def unload() -> None:
    """外部ツールのモジュールと sys.path の追加を片付ける（リロードの前。何度呼んでもよい）。"""
    roots = [Path(p).resolve() for p in _scripts_paths]
    for name, mod in list(sys.modules.items()):
        file = getattr(mod, "__file__", None)
        if not file:
            continue
        try:
            f = Path(file).resolve()
        except (OSError, ValueError):
            continue
        if any(r == f or r in f.parents for r in roots):
            del sys.modules[name]
    for p in _added_to_path:
        while p in sys.path:
            sys.path.remove(p)
    _added_to_path.clear()
    _scripts_paths.clear()
    importlib.invalidate_caches()


def _default_register(tool: Any) -> None:
    from . import shell

    shell.register_tool(tool)


def _default_unavailable(tool_id: str, label: str, text: str) -> Any:
    from . import shell

    return shell._UnavailableTool(tool_id, label, text)


def _load_one(
    lt: external.LoadableTool,
    register: Callable[[Any], None],
    unavailable: Callable[[str, str, str], Any],
) -> None:
    tool_id, label, entry = lt.manifest.id, lt.manifest.name, lt.manifest.entry
    try:
        _add_path(lt.scripts_path)
        module = importlib.import_module(entry)
        create = getattr(module, "create_tool", None)
        if not callable(create):
            raise AttributeError(f"{entry} に create_tool() がありません")
        tool = create()
        if getattr(tool, "id", None) != tool_id:
            _log(f"{label}: create_tool() の id（{getattr(tool, 'id', None)!r}）が tool.json の id（{tool_id}）と違います。{tool_id} のタブにはしません")
            register(unavailable(tool_id, label, f"{label} を読み込めませんでした: create_tool() の id が tool.json の id（{tool_id}）と違います"))
            return
        if not getattr(tool, "label", ""):
            tool.label = label
        register(tool)
    except Exception as exc:  # noqa: BLE001  1 つの外部ツールの失敗で殻・ほかのツールを止めない
        lifecycle.report_error(f"外部ツール {label} を読み込めません", traceback.format_exc())
        register(unavailable(tool_id, label, f"{label} を読み込めませんでした: {exc}"))


def load_external_tools(
    path: str | Path | None = None,
    tdrive_version: str | None = None,
    register: Callable[[Any], None] | None = None,
    unavailable: Callable[[str, str, str], Any] | None = None,
    only: Iterable[str] | None = None,
) -> list[str]:
    """tools.json の外部ツールを読み込んで登録する → 登録した id のリスト。例外は投げない。

    path は tools.json の場所（既定 %LOCALAPPDATA%/TDriveToon/tools.json）。register / unavailable はテスト用の差し替え。
    only を渡すと、その id のツールだけ読み込む（更新ウィンドウの「URL を入力して追加」。ほかのツールを作り直さない）。
    """
    register = register or _default_register
    unavailable = unavailable or _default_unavailable
    lifecycle.on_reload(unload)
    try:
        loadable, skipped, warnings = external.resolve_tools(path, tdrive_version)
    except Exception:  # noqa: BLE001
        lifecycle.report_error("外部ツールの一覧を読めません", traceback.format_exc())
        return []
    for w in warnings:
        _log(w)
    wanted = None if only is None else set(only)
    skipped = [sk for sk in skipped if wanted is None or sk.id in wanted]
    loadable = [lt for lt in loadable if wanted is None or lt.manifest.id in wanted]
    ids: list[str] = []
    for sk in skipped:
        if sk.reason == "無効":
            _log(f"外部ツール {sk.id}: 無効のため読み込みません")
            continue
        _log(f"外部ツール {sk.id}: 読み込めません（{sk.reason}）")
        register(unavailable(sk.id, sk.id, f"{sk.id} を読み込めませんでした: {sk.reason}"))
        ids.append(sk.id)
    for lt in loadable:
        _load_one(lt, register, unavailable)
        ids.append(lt.manifest.id)
    return ids
