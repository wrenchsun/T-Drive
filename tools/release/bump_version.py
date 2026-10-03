"""バージョンを上げてリリースする。docs/06 §5 の手順 5〜6（D-Drive の bump-version.ps1 と同じ引数体系）。

  uv run --no-project python tools/release/bump_version.py --version X.Y.Z --dry-run
  uv run --no-project python tools/release/bump_version.py --version X.Y.Z --tag
  （--part major|minor|patch で現在から自動計算、--no-commit でファイル更新のみ）

行うこと:
  1. check_release と同じ検査（失敗したら中止）
  2. VERSION と Unity パッケージの package.json の version を更新（タグ vX.Y.Z = package.json の version。D-Drive の更新ウィンドウの前提）
  3. ルートとパッケージ（unity/com.tdrive.facial/）の CHANGELOG の [Unreleased] を [X.Y.Z] - 日付 にし、新しい [Unreleased] を作る
  4. パラメータ契約のスナップショット（tests/snapshots/params.json）を更新
  5. "Release vX.Y.Z" でコミット（--tag なら注釈付きタグ vX.Y.Z も）
push はしない（明示的に指示されたときだけ手動で git push --follow-tags）。
"""

from __future__ import annotations

import argparse
import sys

import check_release
import release_lib as rl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--version")
    g.add_argument("--part", choices=rl.PARTS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    # 作業中の他のファイル（例: デザイナーが編集中の look.json）が未コミットでもリリースする。
    # コミットするのは VERSION / CHANGELOG（2 つ）/ package.json / 契約スナップショットだけなので、それらは混ざらない
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args(argv)

    cur = rl.current_version()
    new = a.version or rl.bump(cur, a.part)
    rl.parse(new)
    check_args = ["--version", new] + (["--allow-dirty"] if a.dry_run or a.allow_dirty else [])
    if check_release.main(check_args) != 0:
        print("\n中止: チェックに失敗")
        return 1

    date = rl.today()
    changelog = rl.release_changelog(rl.CHANGELOG.read_text(encoding="utf-8"), new, date)
    pkg_changelog = rl.release_changelog(rl.PACKAGE_CHANGELOG.read_text(encoding="utf-8"), new, date)
    pkg_json = rl.set_package_version(rl.PACKAGE_JSON.read_text(encoding="utf-8"), new)
    print(f"\n{cur} → {new}（{date}）")
    if a.dry_run:
        print("[dry-run] VERSION・package.json・CHANGELOG（ルートとパッケージ）・スナップショットを更新し、コミット" + ("・タグ付け" if a.tag else "") + "します（書き込みはしていません）")
        print(changelog.split("\n## [", 2)[0][:800])
        return 0

    rl.VERSION_FILE.write_text(new + "\n", encoding="utf-8", newline="\n")
    rl.CHANGELOG.write_text(changelog, encoding="utf-8", newline="\n")
    rl.PACKAGE_CHANGELOG.write_text(pkg_changelog, encoding="utf-8", newline="\n")
    rl.PACKAGE_JSON.write_text(pkg_json, encoding="utf-8", newline="\n")
    rl.write_snapshot()
    if a.no_commit:
        print("ファイルを更新しました（コミットなし）")
        return 0
    rl.git("add", *(str(p) for p in (rl.VERSION_FILE, rl.CHANGELOG, rl.PACKAGE_CHANGELOG, rl.PACKAGE_JSON, rl.SNAPSHOT)))
    rl.git("commit", "-m", f"Release v{new}")
    if a.tag:
        rl.git("tag", "-a", f"v{new}", "-m", f"Release v{new}")
    print(f"Release v{new} をコミットしました" + ("（タグ v" + new + "）" if a.tag else "") + "。push は手動で。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
