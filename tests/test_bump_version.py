"""bump_version / check_release の Unity パッケージ対応（一時リポジトリで実行。実リポジトリには触れない）。"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "release"))
import bump_version  # noqa: E402
import check_release  # noqa: E402
import release_lib as rl  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が必要")

PKG_JSON = (
    '{\n  "name": "com.tdrive.facial",\n  "version": "0.4.0",\n  "unity": "6000.3",\n'
    '  "ddriveUpdate": {\n    "compatibleWith": { "com.ddrive.core": "1.4.0" }\n  }\n}\n'
)
ROOT_CL = "# Changelog\n\n## [Unreleased]\n\n### 互換性\n- MINOR: 追加のみ\n\n### 追加\n- root\n\n## [0.4.0] - 2026-01-01\n"
PKG_CL = "# Changelog\n\n## [Unreleased]\n\n### 互換性\n- MINOR: 追加のみ\n\n### 追加\n- pkg\n"


def _git(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8").stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    pkg = tmp_path / "unity" / "com.tdrive.facial"
    pkg.mkdir(parents=True)
    (tmp_path / "VERSION").write_text("0.4.0\n", newline="\n")
    (tmp_path / "CHANGELOG.md").write_text(ROOT_CL, encoding="utf-8", newline="\n")
    (pkg / "package.json").write_text(PKG_JSON, encoding="utf-8", newline="\n")
    (pkg / "CHANGELOG.md").write_text(PKG_CL, encoding="utf-8", newline="\n")
    monkeypatch.setattr(rl, "REPO", tmp_path)
    monkeypatch.setattr(rl, "VERSION_FILE", tmp_path / "VERSION")
    monkeypatch.setattr(rl, "CHANGELOG", tmp_path / "CHANGELOG.md")
    monkeypatch.setattr(rl, "PACKAGE_DIR", pkg)
    monkeypatch.setattr(rl, "PACKAGE_JSON", pkg / "package.json")
    monkeypatch.setattr(rl, "PACKAGE_CHANGELOG", pkg / "CHANGELOG.md")
    monkeypatch.setattr(rl, "SNAPSHOT", tmp_path / "snap" / "params.json")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "tag", "-a", "v0.4.0", "-m", "v0.4.0")
    return tmp_path


def test_set_package_version_keeps_format():
    out = rl.set_package_version(PKG_JSON, "0.5.0")
    assert out == PKG_JSON.replace('"version": "0.4.0"', '"version": "0.5.0"')
    assert "\r" not in out


def test_set_package_version_missing():
    with pytest.raises(ValueError):
        rl.set_package_version('{"name": "x"}', "1.0.0")


def test_release_updates_package_and_commits(repo):
    assert bump_version.main(["--version", "0.5.0", "--tag"]) == 0
    pkg = repo / "unity" / "com.tdrive.facial"
    data = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    assert data["version"] == "0.5.0"
    assert data["ddriveUpdate"] == {"compatibleWith": {"com.ddrive.core": "1.4.0"}}
    assert (pkg / "package.json").read_text(encoding="utf-8") == PKG_JSON.replace("0.4.0", "0.5.0")
    cl = (pkg / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]\n\n### 互換性\n- （リリース前に記入）\n\n## [0.5.0] - " in cl
    assert "### 追加\n- pkg" in cl
    assert (repo / "VERSION").read_text() == "0.5.0\n"
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert {"VERSION", "CHANGELOG.md", "unity/com.tdrive.facial/CHANGELOG.md", "unity/com.tdrive.facial/package.json"} <= set(files)
    assert "v0.5.0" in _git(repo, "tag", "--list")
    assert _git(repo, "show", "v0.5.0:unity/com.tdrive.facial/package.json").count('"0.5.0"') == 1


def test_no_commit_writes_files_only(repo):
    assert bump_version.main(["--version", "0.5.0", "--no-commit", "--allow-dirty"]) == 0
    assert '"version": "0.5.0"' in (repo / "unity/com.tdrive.facial/package.json").read_text(encoding="utf-8")
    assert _git(repo, "log", "--oneline").count("\n") == 1  # コミットは増えない


def test_dry_run_writes_nothing(repo):
    assert bump_version.main(["--version", "0.5.0", "--dry-run"]) == 0
    assert (repo / "unity/com.tdrive.facial/package.json").read_text(encoding="utf-8") == PKG_JSON
    assert (repo / "unity/com.tdrive.facial/CHANGELOG.md").read_text(encoding="utf-8") == PKG_CL


def test_package_changelog_without_compat_fails(repo, capsys):
    (repo / "unity/com.tdrive.facial/CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n### 追加\n- x\n", encoding="utf-8", newline="\n")
    assert bump_version.main(["--version", "0.5.0", "--allow-dirty"]) == 1
    assert "パッケージの CHANGELOG" in capsys.readouterr().out
    assert (repo / "VERSION").read_text() == "0.4.0\n"


def test_package_changelog_placeholder_fails(repo):
    (repo / "unity/com.tdrive.facial/CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### 互換性\n- （リリース前に記入）\n", encoding="utf-8", newline="\n"
    )
    assert check_release.main(["--version", "0.5.0", "--allow-dirty"]) == 1


def test_package_version_mismatch_fails(repo, capsys):
    (repo / "unity/com.tdrive.facial/package.json").write_text(PKG_JSON.replace("0.4.0", "0.3.0"), encoding="utf-8", newline="\n")
    assert check_release.main(["--version", "0.5.0", "--allow-dirty"]) == 1
    assert "package.json の version" in capsys.readouterr().out
