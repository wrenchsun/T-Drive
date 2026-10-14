"""tdrive/external_ops.py（外部ツールの追加・一覧・更新・元に戻す・削除・起動時の確認）。本物の git（bare を origin にした checkout）で確かめる。"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tdrive import external, external_ops, updater

FIX = Path(__file__).resolve().parent / "fixtures" / "external_tool"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")

CHANGELOG = """# tdrive_dummy

## [Unreleased]

## [0.2.0] - 2026-10-14

### 変更
- 機能を足した

### 互換性
- 破壊あり: 出力の形が変わる

## [0.1.0] - 2026-10-01

### 追加
- 初回
"""


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _make_remote(tmp_path, name="remote.git", tool_json=None, tags=("0.1.0", "0.2.0")):
    """ダミーツール（tests/fixtures/external_tool）を bare に push して、タグ v0.1.0 / v0.2.0 を打つ。"""
    work, bare = tmp_path / f"work_{name}", tmp_path / name
    shutil.copytree(FIX, work)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    for ver in tags:
        (work / "VERSION").write_text(ver + "\n", encoding="utf-8")
        (work / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        if tool_json is not None:
            (work / "tool.json").write_text(json.dumps(tool_json), encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", ver)
        _git(work, "tag", "-a", f"v{ver}", "-m", ver)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare.as_posix()


@pytest.fixture
def base(tmp_path):
    b = tmp_path / "TDriveToon"
    b.mkdir()
    return b


@needs_git
def test_add_update_rollback_remove_full_cycle(tmp_path, base):
    url = _make_remote(tmp_path)
    entry = external_ops.add_from_url(url, tag="v0.1.0", base=base, tdrive_version="0.5.0")
    assert (entry.id, entry.dir, entry.tag, entry.url, entry.enabled) == ("tdrive_dummy", "tdrive_dummy", "v0.1.0", url, True)
    checkout = base / "tools" / "tdrive_dummy"
    assert (checkout / "tool.json").is_file() and not list((base / "tools").glob("_adding_*"))
    saved, _ = external.load_tools(external.tools_json_path(base))
    assert [(e.id, e.tag) for e in saved] == [("tdrive_dummy", "v0.1.0")]

    st = external_ops.status(entry, base)
    assert (st["name"], st["version"], st["tag"], st["latest"], st["kind"], st["dev"]) == ("Dummy", "0.1.0", "v0.1.0", "0.2.0", "minor", False)
    assert external_ops.status(entry, base, check_remote=False)["latest"] is None

    text = external_ops.changelog(entry, "v0.1.0", "v0.2.0", base)
    assert "機能を足した" in text and "初回" not in text and text.startswith("破壊あり v0.2.0")

    res = external_ops.update(entry, "v0.2.0", base)
    assert res["from"] == "v0.1.0" and res["to"] == "v0.2.0" and res["restart"] is False
    saved, _ = external.load_tools(external.tools_json_path(base))
    assert (saved[0].tag, saved[0].previous) == ("v0.2.0", "v0.1.0")
    assert external_ops.status(saved[0], base)["kind"] == "same"
    assert "機能を足した" in external_ops.changelog(entry, "0.2.0", "0.1.0", base)  # 戻すと無くなる変更

    external_ops.rollback(saved[0], base)
    saved, _ = external.load_tools(external.tools_json_path(base))
    assert (saved[0].tag, saved[0].previous) == ("v0.1.0", "v0.2.0")
    assert updater.installed_version(checkout) == "0.1.0"

    external_ops.set_enabled(saved[0], False, base)
    assert external.load_tools(external.tools_json_path(base))[0][0].enabled is False
    assert checkout.is_dir()  # 無効化は checkout を残す
    assert external_ops.check_all(base) == []  # 無効は確認しない
    external_ops.set_enabled(saved[0], True, base)
    assert [(e.id, v) for e, v in external_ops.check_all(base)] == [("tdrive_dummy", "0.2.0")]

    external_ops.remove(saved[0], base)
    assert not checkout.exists() and external.load_tools(external.tools_json_path(base))[0] == []


@needs_git
def test_add_without_tag_pins_latest(tmp_path, base):
    entry = external_ops.add_from_url(_make_remote(tmp_path), base=base, tdrive_version="0.5.0")
    assert entry.tag == "v0.2.0"


@needs_git
def test_add_failures_leave_nothing(tmp_path, base):
    ok = _make_remote(tmp_path)
    # clone できない
    with pytest.raises(external_ops.ExternalOpsError):
        external_ops.add_from_url((tmp_path / "nothing.git").as_posix(), base=base, tdrive_version="0.5.0")
    # tool.json が無い
    bare = tmp_path / "plain.git"
    work = tmp_path / "plain"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "x")
    _git(work, "tag", "-a", "v1.0.0", "-m", "x")
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    with pytest.raises(external_ops.ExternalOpsError, match="tool.json"):
        external_ops.add_from_url(bare.as_posix(), base=base, tdrive_version="0.5.0")
    # requires.tdrive を満たさない
    needs_new = _make_remote(tmp_path, "new.git", tool_json={"id": "tdrive_new", "name": "New", "entry": "tdrive_dummy.tool", "requires": {"tdrive": "9.0.0"}})
    with pytest.raises(external_ops.ExternalOpsError, match="T-Drive 9.0.0"):
        external_ops.add_from_url(needs_new, base=base, tdrive_version="0.5.0")
    # リリースタグが無い
    notag = _make_remote(tmp_path, "notag.git", tags=())
    with pytest.raises(external_ops.ExternalOpsError):
        external_ops.add_from_url(notag, base=base, tdrive_version="0.5.0")
    with pytest.raises(external_ops.ExternalOpsError):
        external_ops.add_from_url(ok, tag="latest", base=base, tdrive_version="0.5.0")
    assert external.load_tools(external.tools_json_path(base))[0] == []
    assert list((base / "tools").iterdir()) == []
    # 同じ id は二重に追加しない
    external_ops.add_from_url(ok, base=base, tdrive_version="0.5.0")
    with pytest.raises(external_ops.ExternalOpsError, match="導入済み"):
        external_ops.add_from_url(ok, base=base, tdrive_version="0.5.0")
    assert [p.name for p in (base / "tools").iterdir()] == ["tdrive_dummy"]


@needs_git
def test_dev_tool_is_not_updated_or_deleted(tmp_path, base):
    url = _make_remote(tmp_path)
    mine = tmp_path / "mine"
    subprocess.run(["git", "clone", "-q", url, str(mine)], check=True)  # ブランチ上 = 開発用
    entry = external.ToolEntry(id="tdrive_dummy", url=url, dir=str(mine), tag="")
    external.upsert_tool(entry, external.tools_json_path(base))
    st = external_ops.status(entry, base)
    assert st["dev"] is True and st["latest"] == "0.2.0"
    with pytest.raises(external_ops.ExternalOpsError, match="開発用"):
        external_ops.update(entry, "v0.2.0", base)
    with pytest.raises(external_ops.ExternalOpsError, match="開発用"):
        external_ops.rollback(entry, base)
    assert external_ops.check_all(base) == [(entry, None)]  # 開発用は確認しない
    external_ops.remove(entry, base)
    assert mine.is_dir() and (mine / "tool.json").is_file()  # 自分の clone は消さない
    assert external.load_tools(external.tools_json_path(base))[0] == []


@needs_git
def test_release_checkout_with_branch_is_dev_and_unreachable_remote_is_silent(tmp_path, base):
    entry = external_ops.add_from_url(_make_remote(tmp_path), base=base, tdrive_version="0.5.0")
    checkout = base / "tools" / entry.dir
    _git(checkout, "checkout", "-q", "main")
    assert external_ops.status(entry, base)["dev"] is True
    shutil.rmtree(tmp_path / "remote.git", onerror=lambda f, p, e: (__import__("os").chmod(p, 0o700), f(p)))
    st = external_ops.status(entry, base)
    assert st["latest"] is None and st["kind"] is None  # 失敗は None
    assert external_ops.check_all(base) == [(entry, None)]
    gone = external.ToolEntry(id="tdrive_gone", dir="tdrive_gone")
    assert "見つかりません" in external_ops.status(gone, base)["problem"]


def test_startup_message_merges_body_and_tools():
    f = external_ops.startup_message
    assert f(None, []) is None
    assert f("0.6.0", []) == "T-Drive Toon の新しい版 v0.6.0 があります（<hl>Tools › T-Drive › 更新…</hl>）"  # 本体だけは従来の文言
    msg = f("0.6.0", [("MS2026 グレーボックス", "1.2.0", "1.3.0"), ("Dummy", "0.1.0", "0.2.0")])
    assert msg.startswith("更新があります（本体 v0.6.0 / MS2026 グレーボックス v1.3.0 / Dummy v0.2.0）") and "更新…" in msg
    assert f(None, [("Dummy", "0.1.0", "0.2.0")]).startswith("更新があります（Dummy v0.2.0）")


@needs_git
def test_pending_updates_lists_only_newer_tools(tmp_path, base):
    url = _make_remote(tmp_path)
    external_ops.add_from_url(url, tag="v0.1.0", base=base, tdrive_version="0.5.0")
    assert external_ops.pending_updates(base) == [("Dummy", "0.1.0", "0.2.0")]
    entry = external.load_tools(external.tools_json_path(base))[0][0]
    external_ops.update(entry, "v0.2.0", base)
    assert external_ops.pending_updates(base) == []
