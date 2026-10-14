"""外部ツールの導入・更新の操作（docs/13 §6.3）。Maya / Qt 非依存（更新ウィンドウの「外部ツール」区画が呼ぶ）。

    add_from_url   URL を clone → tool.json の id でフォルダ名を決める → requires を判定 → tools.json に追記
    status         一覧の 1 行ぶん（名前・今の版・固定タグ・最新の版・上げ幅・開発用か）
    update / rollback / changelog   本体と同じ処理を、ツールの checkout（Git(repo=…)）で
    set_enabled / remove            無効化（checkout は残す）/ 削除（tools.json から外し checkout を消す）
    check_all      起動時の確認（各ツールに git ls-remote）

- 失敗は `ExternalOpsError`（`updater.GitError` の仲間なので、画面は GitError を拾えばよい）
- base は TDriveToon フォルダ（省略で %LOCALAPPDATA%\\TDriveToon）。tools.json は <base>/tools.json、checkout は <base>/tools/<dir>
- 開発用（`dir` が絶対パス / ブランチ上・変更あり）は更新・元に戻すをしない。削除でも開発用の clone は消さない
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import uuid
from pathlib import Path
from typing import Any

from . import external, updater
from .external import ToolEntry
from .updater import Git, GitError


class ExternalOpsError(GitError):
    """外部ツールの導入・更新の失敗（メッセージはそのまま画面に出せる）。"""


def _base(base: str | Path | None) -> Path:
    return Path(base) if base else external.base_dir()


def _json_path(base: str | Path | None) -> Path:
    return external.tools_json_path(_base(base))


def _rmtree(path: Path) -> None:
    """checkout を消す。.git の中は読み取り専用なので、Windows では属性を外してやり直す。"""

    def onerror(func, p, _exc):  # noqa: ANN001
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onerror)
    else:
        shutil.rmtree(path, onerror=onerror)


def find_entry(tool_id: str, base: str | Path | None = None) -> ToolEntry | None:
    entries, _ = external.load_tools(_json_path(base))
    return next((e for e in entries if e.id == tool_id), None)


def _checkout(entry: ToolEntry, base: str | Path | None) -> Path:
    return external.checkout_path(entry, _base(base))


def _require_updatable(entry: ToolEntry, base: str | Path | None) -> Git:
    if external.is_dev_dir(entry.dir):
        raise ExternalOpsError(f"{entry.id} は開発用（絶対パスの clone）です。更新は git で行ってください")
    checkout = _checkout(entry, base)
    if not checkout.is_dir():
        raise ExternalOpsError(f"{entry.id} の checkout が見つかりません（{checkout}）")
    return Git(checkout)


# ---------------------------------------------------------------- 追加
def add_from_url(
    url: str,
    tag: str | None = None,
    base: str | Path | None = None,
    tdrive_version: str | None = None,
) -> ToolEntry:
    """URL の外部ツールを導入する。clone できない・tool.json が無い・id が重複・requires.tdrive を満たさないときは何も残さず ExternalOpsError。"""
    url = (url or "").strip()
    if not url:
        raise ExternalOpsError("URL が空です")
    tag = (tag or "").strip() or None
    if tag and updater.parse_version(tag) is None:
        raise ExternalOpsError(f"タグは vX.Y.Z の形で指定してください（{tag}）")
    root = _base(base)
    tools_dir = root / external.TOOLS_DIR
    tools_dir.mkdir(parents=True, exist_ok=True)
    temp = tools_dir / f"_adding_{uuid.uuid4().hex[:8]}"
    try:
        try:
            git = updater.clone(url, temp, tag=tag)
        except GitError as exc:
            raise ExternalOpsError(f"取り込めませんでした: {exc}") from exc
        manifest, reason = external.read_manifest(temp)
        if manifest is None:
            raise ExternalOpsError(f"外部ツールとして使えません: {reason}")
        existing, _ = external.load_tools(_json_path(root))
        if any(e.id == manifest.id for e in existing):
            raise ExternalOpsError(f"{manifest.id} は導入済みです（更新するか、削除してから追加してください）")
        reason = external.check_requires(manifest, tdrive_version)
        if reason:
            raise ExternalOpsError(f"{manifest.name} は追加できません: {reason}")
        final = tools_dir / manifest.id
        if final.exists():
            raise ExternalOpsError(f"{final} が既にあります（tools.json に無いフォルダです。消してから追加してください）")
        git_tag = git.current_tag() or ""
        temp.rename(final)
        entry = ToolEntry(id=manifest.id, url=url, dir=manifest.id, tag=git_tag)
        external.upsert_tool(entry, _json_path(root))
        return entry
    finally:
        if temp.exists():
            _rmtree(temp)


# ---------------------------------------------------------------- 一覧
def status(entry: ToolEntry, base: str | Path | None = None, check_remote: bool = True) -> dict[str, Any]:
    """一覧の 1 行ぶん。check_remote=False ならネットワークに出ない（latest は None）。

    キー: id / name / version（今の版 X.Y.Z）/ tag（固定タグ）/ latest（最新の版 X.Y.Z または None）/
    kind（上げ幅 major・minor・patch・same・older または None）/ dev / enabled / problem（読めない理由。無ければ ""）
    """
    checkout = _checkout(entry, base)
    exists = checkout.is_dir()
    manifest, reason = external.read_manifest(checkout) if exists else (None, f"checkout が見つかりません（{checkout}）")
    version = updater.installed_version(checkout) if exists else ""
    git = Git(checkout)
    tag = (git.current_tag() or entry.tag) if exists else entry.tag
    latest: str | None = None
    if check_remote and exists:
        try:
            versions = git.remote_versions()
            latest = updater.fmt(versions[0]) if versions else None
        except GitError:
            latest = None
    cur_v, latest_v = updater.parse_version(version), updater.parse_version(latest or "")
    return {
        "id": entry.id,
        "name": manifest.name if manifest else entry.id,
        "version": version,
        "tag": tag or "",
        "latest": latest,
        "kind": updater.bump_kind(cur_v, latest_v) if cur_v and latest_v else None,
        "dev": external.is_dev_dir(entry.dir) or (exists and updater.install_kind(git) != "release"),
        "enabled": entry.enabled,
        "problem": reason,
    }


# ---------------------------------------------------------------- 更新 / 元に戻す
def _record(entry: ToolEntry, git: Git, base: str | Path | None) -> ToolEntry:
    entry.tag = git.current_tag() or entry.tag
    entry.previous = str(updater.read_state(git).get("previous", ""))
    external.upsert_tool(entry, _json_path(base))
    return entry


def update(entry: ToolEntry, tag: str, base: str | Path | None = None) -> dict[str, Any]:
    """tag（vX.Y.Z）に更新し、tools.json の tag / previous を更新する。updater.update_to の結果（from / to / restart / changed）を返す。"""
    git = _require_updatable(entry, base)
    res = updater.update_to(git, tag)
    _record(entry, git, base)
    return res


def rollback(entry: ToolEntry, base: str | Path | None = None) -> dict[str, Any]:
    """1 段前の版に戻す（もう一度呼ぶと戻す前に戻る）。tools.json を更新する。"""
    git = _require_updatable(entry, base)
    res = updater.rollback(git)
    _record(entry, git, base)
    return res


def changelog(entry: ToolEntry, current: str, target: str, base: str | Path | None = None) -> str:
    """current → target の変更点（CHANGELOG.md の節を新しい順。「破壊あり」は先頭にまとめる）。読めなければ空文字。

    current / target は vX.Y.Z または X.Y.Z。target が古い（戻す）ときは、戻すと無くなる変更を返す。
    """
    cur, tgt = updater.parse_version(current), updater.parse_version(target)
    if cur is None or tgt is None or cur == tgt:
        return ""
    low, high = (cur, tgt) if tgt > cur else (tgt, cur)
    git = Git(_checkout(entry, base))
    ref = f"v{updater.fmt(high)}"
    try:
        text = git.show(ref, "CHANGELOG.md")
    except GitError:
        try:  # まだ取り込んでいないタグ（最新の版）。タグだけ取り込んで読み直す
            git.fetch_tags()
            text = git.show(ref, "CHANGELOG.md")
        except GitError:
            return ""
    sections = updater.changelog_between(text, low, high)
    breaking = [
        f"破壊あり v{updater.fmt(v)}: {notes}"
        for v, body in sections
        if "破壊あり" in (notes := updater.compat_notes(body))
    ]
    parts = (["\n".join(breaking)] if breaking else []) + [body for _, body in sections]
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 有効 / 削除
def set_enabled(entry: ToolEntry, flag: bool, base: str | Path | None = None) -> ToolEntry:
    entry.enabled = bool(flag)
    external.upsert_tool(entry, _json_path(base))
    return entry


def remove(entry: ToolEntry, base: str | Path | None = None) -> None:
    """tools.json から外し、checkout を消す。開発用（絶対パスの dir）の clone は消さない。"""
    external.remove_tool(entry.id, _json_path(base))
    if external.is_dev_dir(entry.dir):
        return
    checkout = _checkout(entry, base)
    tools_dir = (_base(base) / external.TOOLS_DIR).resolve()
    if checkout.is_dir() and tools_dir in checkout.resolve().parents:  # tools/ の外は消さない（dir に .. が入っていても）
        _rmtree(checkout)


# ---------------------------------------------------------------- 起動時の確認
def check_all(base: str | Path | None = None) -> list[tuple[ToolEntry, str | None]]:
    """有効なツールごとに git ls-remote して最新の版（X.Y.Z）を返す。失敗・開発用・checkout なしは None（黙る）。"""
    entries, _ = external.load_tools(_json_path(base))
    out: list[tuple[ToolEntry, str | None]] = []
    for e in entries:
        if not e.enabled:
            continue
        latest: str | None = None
        checkout = _checkout(e, base)
        if checkout.is_dir() and not external.is_dev_dir(e.dir):
            try:
                versions = Git(checkout).remote_versions()
                latest = updater.fmt(versions[0]) if versions else None
            except GitError:
                latest = None
        out.append((e, latest))
    return out


def pending_updates(base: str | Path | None = None) -> list[tuple[str, str, str]]:
    """更新のあるツール（名前, 今の版, 最新の版）。起動時の確認用（失敗したツールは黙って除く）。"""
    out: list[tuple[str, str, str]] = []
    for e, latest in check_all(base):
        latest_v = updater.parse_version(latest or "")
        cur_v = updater.parse_version(updater.installed_version(_checkout(e, base)))
        if latest_v is None or cur_v is None or latest_v <= cur_v:
            continue
        manifest, _ = external.read_manifest(_checkout(e, base))
        out.append((manifest.name if manifest else e.id, updater.fmt(cur_v), updater.fmt(latest_v)))
    return out


def startup_message(body_latest: str | None, tools: list[tuple[str, str, str]], hint: str = "Tools › T-Drive › 更新…") -> str | None:
    """起動時の「更新があります」を 1 つにまとめる。更新が無ければ None。

    本体だけのときは従来の文言のまま。ツールがあるときは「更新があります（本体 vX / ツール名 vY …）」。
    """
    if not tools:
        return f"T-Drive Toon の新しい版 v{body_latest} があります（<hl>{hint}</hl>）" if body_latest else None
    items = ([f"本体 v{body_latest}"] if body_latest else []) + [f"{name} v{latest}" for name, _cur, latest in tools]
    return f"更新があります（{' / '.join(items)}）（<hl>{hint}</hl>）"
