import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "release"))
import release_lib as rl  # noqa: E402


@pytest.mark.parametrize("v,part,out", [("1.2.3", "major", "2.0.0"), ("1.2.3", "minor", "1.3.0"), ("1.2.3", "patch", "1.2.4")])
def test_bump(v, part, out):
    assert rl.bump(v, part) == out


def test_part_between():
    assert rl.part_between("1.2.3", "2.0.0") == "major"
    assert rl.part_between("1.2.3", "1.3.0") == "minor"
    assert rl.part_between("1.2.3", "1.2.4") == "patch"
    with pytest.raises(ValueError):
        rl.part_between("1.2.3", "1.2.2")


def test_required_part_is_mechanical():
    old = {"_ToonA": "float", "_ToonB": "color"}
    assert rl.required_part(old, dict(old), 1)[0] == "patch"
    assert rl.required_part(old, {**old, "_ToonC": "float"}, 1)[0] == "minor"
    assert rl.required_part(old, {"_ToonA": "float"}, 1)[0] == "major"  # 削除
    assert rl.required_part(old, {"_ToonA": "color", "_ToonB": "color"}, 1)[0] == "major"  # 型変更
    assert rl.required_part(old, {"_ToonA": "float"}, 0)[0] == "minor"  # 0.x の間は MINOR で可


def test_release_changelog_moves_unreleased():
    text = "# Changelog\n\n## [Unreleased]\n\n### 追加\n- x\n\n### 互換性\n- 追加のみ\n\n## [0.1.0] - 2026-01-01\n"
    out = rl.release_changelog(text, "0.2.0", "2026-09-28")
    assert "## [Unreleased]\n\n### 互換性\n- （リリース前に記入）\n\n## [0.2.0] - 2026-09-28\n\n### 追加\n- x" in out
    assert out.count("## [0.1.0]") == 1


def test_snapshot_matches_contract_or_is_baseline():
    """スナップショットは直近リリース時点の契約。未作成（初回前）か、現在の契約から機械的に上げ幅が出せること。"""
    snap = rl.load_snapshot()
    part, _ = rl.required_part(snap, rl.contract(), 0)
    assert part in rl.PARTS
