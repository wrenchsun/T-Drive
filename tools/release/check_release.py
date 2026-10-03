"""リリース前チェック（検査のみ。何も書き換えない）。docs/06 §5 の手順 2。

  uv run --no-project python tools/release/check_release.py [--part major|minor|patch | --version X.Y.Z] [--allow-dirty]

検査:
  1. 作業ツリーがクリーン
  2. CHANGELOG（ルートと unity/com.tdrive.facial/）の [Unreleased] に「### 互換性」があり、記入されている。package.json の version = VERSION
  3. パラメータ契約とスナップショット（tests/snapshots/params.json）の差分から必要な上げ幅を出し、指定と矛盾しない
  4. looks/*/look.json が検証を通る
終了コード: 問題なし 0 / 問題あり 1
"""

from __future__ import annotations

import argparse
import sys

import release_lib as rl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--part", choices=rl.PARTS)
    g.add_argument("--version")
    ap.add_argument("--allow-dirty", action="store_true", help="作業ツリーの未コミット変更を許す（試し打ち用）")
    a = ap.parse_args(argv)

    problems: list[str] = []
    cur = rl.current_version()
    print(f"現在のバージョン: {cur}")

    if not a.allow_dirty and not rl.working_tree_clean():
        problems.append("作業ツリーに未コミットの変更がある")

    problems += rl.changelog_problems(rl.CHANGELOG.read_text(encoding="utf-8"), "CHANGELOG")
    if not rl.PACKAGE_CHANGELOG.exists():
        problems.append("Unity パッケージの CHANGELOG（unity/com.tdrive.facial/CHANGELOG.md）が無い")
    else:
        problems += rl.changelog_problems(rl.PACKAGE_CHANGELOG.read_text(encoding="utf-8"), "パッケージの CHANGELOG")
    if not rl.PACKAGE_JSON.exists():
        problems.append("Unity パッケージの package.json が無い")
    else:
        pv = rl.package_version(rl.PACKAGE_JSON.read_text(encoding="utf-8"))
        if pv != cur:
            problems.append(f"package.json の version（{pv}）が VERSION（{cur}）と違う（同時にリリースする）")

    need, notes = rl.required_part(rl.load_snapshot(), rl.contract(), rl.parse(cur)[0])
    print(f"パラメータ契約の変化から必要な上げ幅: {need}")
    for n in notes:
        print(f"  - {n}")
    if a.version and rl.is_initial_release(a.version):
        print("初回リリース（タグ未作成）: 上げ幅の検査はしない。以後はこの時点の契約が比較の基準になる")
    elif a.part or a.version:
        part = a.part or rl.part_between(cur, a.version)
        if rl.PARTS.index(part) > rl.PARTS.index(need):
            problems.append(f"上げ幅 {part} では足りない（契約の変化により {need} 以上が必要）")

    for path, errors in rl.look_errors().items():
        problems += [f"{path}: {e}" for e in errors]

    if problems:
        print("\n問題:")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\nOK: リリースできます")
    return 0


if __name__ == "__main__":
    sys.exit(main())
