"""T-Drive: Maya 2026 用ツール群の殻（2 段タブのウィンドウ・メニュー・プロジェクト・更新・Maya との境界）。

中身のツールは tdrive_toon（セルルック Look Development）と tdrive_facial（FacialController）。
殻は Tool（tdrive.tool）だけを知り、ツールの中身を知らない（docs/15 §2.1）。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_version() -> str:
    try:
        return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


__version__ = _read_version()
