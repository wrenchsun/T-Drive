"""ツール本体の更新（docs/13 §3）。git を使う部分は一時フォルダの本物のリポジトリで確かめる。"""

import shutil
import subprocess

import pytest

from tdrive_toon import updater

CHANGELOG = """# Changelog

## [Unreleased]

## [1.0.0] - 2026-10-10

### 変更
- 大きな変更

### 互換性
- MAJOR: パラメータ名を変更（移行ガイド docs/migrations/v1.md）

## [0.2.0] - 2026-10-01

### 追加
- 新機能

### 互換性
- 追加のみ

## [0.1.1] - 2026-09-29

### 修正
- 不具合

### 互換性
- 触れていない

## [0.1.0] - 2026-09-28

### 追加
- 初回
"""


def test_versions_and_bump_kind():
    assert updater.parse_version("v1.2.3") == (1, 2, 3) and updater.parse_version("1.2") is None
    assert updater.bump_kind((0, 1, 0), (0, 1, 1)) == "patch"
    assert updater.bump_kind((0, 1, 0), (0, 2, 0)) == "minor"
    assert updater.bump_kind((0, 1, 0), (1, 0, 0)) == "major"
    assert updater.bump_kind((0, 2, 0), (0, 1, 0)) == "older"


def test_parse_ls_remote_keeps_release_tags_only():
    text = "\n".join([
        "aaa\trefs/tags/v0.1.0", "bbb\trefs/tags/v0.1.0^{}", "ccc\trefs/tags/v0.2.0",
        "ddd\trefs/tags/look/unitychan/v1.0.0", "eee\trefs/tags/v1.0.0-rc1", "fff\trefs/heads/main",
    ])
    assert updater.parse_ls_remote(text) == [(0, 2, 0), (0, 1, 0)]  # ルックのタグ・rc・ブランチは除く、新しい順


def test_changelog_between_and_compat():
    got = updater.changelog_between(CHANGELOG, (0, 1, 0), (1, 0, 0))
    assert [v for v, _ in got] == [(1, 0, 0), (0, 2, 0), (0, 1, 1)]
    assert "MAJOR" in updater.compat_notes(got[0][1]) and updater.compat_notes(got[1][1]) == "- 追加のみ"
    assert updater.changelog_between(CHANGELOG, (0, 2, 0), (0, 2, 0)) == []


def test_update_mod_version():
    mod = "+ MAYAVERSION:2026 TDriveToon 0.1.0 C:/x/T-Drive/maya\nscripts: scripts\n"
    assert updater.update_mod_version(mod, "0.2.0").startswith("+ MAYAVERSION:2026 TDriveToon 0.2.0 C:/x/T-Drive/maya")


def test_should_auto_check_once_a_day():
    assert updater.should_auto_check({}, now=1e9)
    assert not updater.should_auto_check({"lastCheck": 1e9 - 3600}, now=1e9)
    assert not updater.should_auto_check({"autoCheck": False}, now=1e9)


# ---------------------------------------------------------------- 本物の git で: 導入 → 更新 → 元に戻す
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def remote_and_install(tmp_path):
    """GitHub 役（bare）に v0.1.0 / v0.2.0 / main の途中のコミット、デザイナーの導入は v0.1.0 に固定。"""
    work, bare, inst = tmp_path / "work", tmp_path / "remote.git", tmp_path / "install"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    for ver, extra in (("0.1.0", None), ("0.2.0", "maya/scripts/userSetup.py"), ("0.3.0-dev", None)):
        (work / "VERSION").write_text(ver + "\n", encoding="utf-8")
        (work / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        if extra:
            (work / extra).parent.mkdir(parents=True, exist_ok=True)
            (work / extra).write_text("# changed\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", ver)
        if "dev" not in ver:
            _git(work, "tag", "-a", f"v{ver}", "-m", ver)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(inst)], check=True)
    _git(inst, "-c", "advice.detachedHead=false", "checkout", "-q", "v0.1.0")
    return updater.Git(inst)


@needs_git
def test_release_install_update_and_rollback(remote_and_install):
    git = remote_and_install
    assert updater.install_kind(git) == "release" and git.current_tag() == "v0.1.0"
    assert git.remote_versions() == [(0, 2, 0), (0, 1, 0)]  # main の途中（0.3.0-dev）は配布されない
    res = updater.update_to(git, "v0.2.0")
    assert git.current_tag() == "v0.2.0" and updater.installed_version(git.repo) == "0.2.0"
    assert res["restart"]  # userSetup.py が変わる更新は再起動を案内
    updater.rollback(git)  # 1 段の入れ替え
    assert git.current_tag() == "v0.1.0"
    updater.rollback(git)
    assert git.current_tag() == "v0.2.0"


@needs_git
def test_dev_install_and_local_changes_are_not_updated(remote_and_install):
    git = remote_and_install
    (git.repo / "VERSION").write_text("hacked\n", encoding="utf-8")
    assert updater.install_kind(git) == "dev"
    with pytest.raises(updater.GitError):
        updater.update_to(git, "v0.2.0")
    _git(git.repo, "checkout", "-q", "--", "VERSION")
    _git(git.repo, "checkout", "-q", "main")  # ブランチ上 = 開発用
    assert updater.install_kind(git) == "dev"
