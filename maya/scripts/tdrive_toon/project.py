"""プロジェクトフォルダ（デザイナーのデータの置き場所。docs/13 §1）。Maya 非依存。

ツール本体（REPO_ROOT。リリースでは git のタグに固定され、デザイナーは触らない）とデータを分ける:

    <プロジェクト>/looks/<キャラクター>/look.json   Look
    <プロジェクト>/looks/_env/*.json               環境プロファイル（ツール同梱の同名より優先）
    <プロジェクト>/build/unity/                    Unity 出力
    <プロジェクト>/captures/                       キャプチャ
    <プロジェクト>/.tdrive/project.json            プロジェクトの設定（最後に更新を適用したツールの版 など）

場所の決め方: set_root() で選んだもの > 環境変数 TDRIVE_PROJECT > ツール本体（開発用。このリポジトリで作業するとき）。
選んだ場所は Maya の設定（optionVar）に保存する（呼び出し側。session.load_project_preference）。
テクスチャの相対パスはプロジェクト基準で、シーンには $TDRIVE_PROJECT/… と書く（どの PC でも開ける）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import REPO_ROOT

ENV = "TDRIVE_PROJECT"
CONFIG_REL = Path(".tdrive") / "project.json"

_root: Path | None = None


def root() -> Path:
    """今のプロジェクトフォルダ。"""
    if _root is not None:
        return _root
    env = os.environ.get(ENV)
    return Path(env) if env else REPO_ROOT


def set_root(path: str | Path | None) -> Path:
    """プロジェクトフォルダを選ぶ（None でツール本体 = 開発用に戻す）。$TDRIVE_PROJECT も合わせる。"""
    global _root
    _root = Path(path).resolve() if path else None
    os.environ[ENV] = root().as_posix()
    return root()


def is_tool_repo() -> bool:
    """プロジェクト = ツール本体（開発用）か。"""
    try:
        return root().resolve() == REPO_ROOT.resolve()
    except OSError:
        return False


def looks_dir() -> Path:
    return root() / "looks"


def export_dir() -> Path:
    return root() / "build" / "unity"


def capture_dir() -> Path:
    return root() / "captures"


def profile_dirs() -> list[Path]:
    """環境プロファイルを探す場所（先にあるものが優先）。プロジェクト → ツール同梱。"""
    dirs = [looks_dir() / "_env", REPO_ROOT / "looks" / "_env"]
    out: list[Path] = []
    for d in dirs:
        if all(d.resolve() != o.resolve() for o in out):
            out.append(d)
    return out


def to_project_path(path: str | Path) -> str:
    """プロジェクト内ならプロジェクト基準の相対パス（/ 区切り）、外なら絶対パスのまま。"""
    p = Path(path)
    try:
        return p.resolve().relative_to(root().resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def from_project_path(path: str) -> str:
    p = Path(path)
    return (p if p.is_absolute() else root() / p).as_posix()


# ---------------------------------------------------------------- 設定（.tdrive/project.json）
def config_path() -> Path:
    return root() / CONFIG_REL


def read_config() -> dict[str, Any]:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_config(**values: Any) -> dict[str, Any]:
    cfg = read_config()
    cfg.update(values)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return cfg


# 起動直後から $TDRIVE_PROJECT を使えるようにする（シーンのファイルノードが参照する）
os.environ.setdefault(ENV, root().as_posix())
