"""リリース運用の共通処理（docs/06_release_versioning.md）。D-Drive の Tools/Release と同じ手順を Python で行う。

互換性の面のうち機械的に検査できるもの:
  - パラメータ契約（名前・型）… tests/snapshots/params.json と現在の params.py を比べる
  - Look 定義スキーマ … looks/*/look.json が検証を通るか
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO / "VERSION"
CHANGELOG = REPO / "CHANGELOG.md"
SNAPSHOT = REPO / "tests" / "snapshots" / "params.json"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PARTS = ("major", "minor", "patch")

sys.path.insert(0, str(REPO / "maya" / "scripts"))


# ---------------------------------------------------------------- バージョン


def current_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def parse(v: str) -> tuple[int, int, int]:
    m = SEMVER.match(v)
    if not m:
        raise ValueError(f"SemVer ではない: {v}")
    return int(m[1]), int(m[2]), int(m[3])


def bump(v: str, part: str) -> str:
    major, minor, patch = parse(v)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(part)


def is_initial_release(version: str) -> bool:
    """まだ一度もリリース（タグ付け）していない現在のバージョンをそのまま出す場合。"""
    return version == current_version() and not git("tag", "--list", f"v{version}").strip()


def part_between(old: str, new: str) -> str:
    o, n = parse(old), parse(new)
    if n == o and is_initial_release(new):
        return "patch"  # 初回リリース: 比較対象が無いので上げ幅の検査はしない
    if n <= o:
        raise ValueError(f"新しいバージョン {new} が現在の {old} 以下")
    if n[0] != o[0]:
        return "major"
    if n[1] != o[1]:
        return "minor"
    return "patch"


# ---------------------------------------------------------------- パラメータ契約


def contract() -> dict[str, str]:
    from tdrive_toon import params

    return {p.unity: p.kind for p in params.SPECIFIC_PARAMS}


def load_snapshot() -> dict[str, str]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}


def write_snapshot() -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(contract(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def required_part(old: dict[str, str], new: dict[str, str], major_version: int) -> tuple[str, list[str]]:
    """スナップショットとの差分から必要な上げ幅を決める（判断ではなく機械的に。docs/06 §2）。

    0.x の間は破壊的変更も MINOR で出せる（CHANGELOG の 0.x 規則）。
    """
    notes = []
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    added = sorted(set(new) - set(old))
    notes += [f"削除: {k}" for k in removed] + [f"型変更: {k} {old[k]} → {new[k]}" for k in changed]
    notes += [f"追加: {k}" for k in added]
    if removed or changed:
        return ("major" if major_version >= 1 else "minor"), notes
    if added:
        return "minor", notes
    return "patch", notes


# ---------------------------------------------------------------- CHANGELOG


def unreleased_section(text: str) -> str | None:
    m = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    return m.group(1) if m else None


def release_changelog(text: str, version: str, date: str) -> str:
    """[Unreleased] を [version] - date にし、新しい空の [Unreleased] を先頭に置く。"""
    if unreleased_section(text) is None:
        raise ValueError("CHANGELOG に ## [Unreleased] が無い")
    fresh = "## [Unreleased]\n\n### 互換性\n- （リリース前に記入）\n\n"
    return text.replace("## [Unreleased]\n", fresh + f"## [{version}] - {date}\n", 1)


def today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- git


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} に失敗: {r.stderr.strip()}")
    return r.stdout


def working_tree_clean() -> bool:
    return git("status", "--porcelain").strip() == ""


# ---------------------------------------------------------------- Look


def look_errors() -> dict[str, list[str]]:
    from tdrive_toon import look

    out = {}
    for p in sorted((REPO / "looks").glob("*/look.json")):
        errors = look.validate(json.loads(p.read_text(encoding="utf-8")))
        if errors:
            out[str(p.relative_to(REPO))] = errors
    return out
