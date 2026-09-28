"""T-Drive Toon: Maya 2026 用セルルック キャラクター Look Development ツール。

Maya 非依存モジュール（params / look / parts_rules）と、Maya 依存モジュール
（parts / preview / ab / ui / menu）に分かれる。Maya 非依存側は tests/ から単体テストする。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_version() -> str:
    try:
        return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


__version__ = _read_version()
